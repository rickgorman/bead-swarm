from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
sys.path.insert(0, str(ROOT))

from beadswarm import amutil, bd  # noqa: E402
from beadswarm.scenario import load_scenario, materialize  # noqa: E402
from beadswarm.worker import AGENT_NAMES  # noqa: E402


def require_bins(*names: str) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise RuntimeError(f"missing binaries: {', '.join(missing)}")


def bd_path() -> str:
    path = shutil.which("bd")
    if not path:
        raise RuntimeError("bd not on PATH")
    return path


def write_executable(path: Path, contents: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(0o755)
    return path


def isolated_env(tmp: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = amutil.isolated_am_env(tmp)
    env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
    env["BEAD_SWARM_BR_BIN"] = bd_path()
    env["BR_BIN"] = bd_path()
    env["BD_BIN"] = bd_path()
    env["BEAD_SWARM_AM_BIN"] = shutil.which("am") or "am"
    env["AM_BIN"] = env["BEAD_SWARM_AM_BIN"]
    env["BEAD_SWARM_SEAT_CACHE"] = str(tmp / "seat-cache.json")
    env["BEAD_SWARM_CLAUDE_BIN"] = str(tmp / "no-such-claude")
    env["BEAD_SWARM_CODEX_BIN"] = str(tmp / "no-such-codex")
    env["BEAD_SWARM_GROK_BIN"] = str(tmp / "no-such-grok")
    env["BEAD_SWARM_CURSOR_BIN"] = str(tmp / "no-such-cursor")
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("BEAD_SWARM_SKIP_BV", "1")
    env.setdefault("BEAD_SWARM_RESERVE_SECONDS", "45")
    for key in (
        "PLANNING_MODELS",
        "BUILDING_MODELS",
        "BEAD_SWARM_PLANNING_MODELS",
        "BEAD_SWARM_BUILDING_MODELS",
        "BEAD_SWARM_SEAT_CACHE_TTL",
    ):
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


def warmup_am(lab: Path, env: dict[str, str]) -> None:
    """Create the isolated mailbox once so parallel workers do not race migrations."""
    amutil.register_agent(
        lab,
        "AmberLake",
        env=env,
        program="bead-swarm",
        model="host-launcher",
        task="warmup",
        am_bin=env.get("AM_BIN", "am"),
    )
    for name in AGENT_NAMES:
        amutil.register_agent(lab, name, env=env, am_bin=env.get("AM_BIN", "am"))


def make_lab(tmp: Path, scenario: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    spec = load_scenario(scenario)
    lab = tmp / "lab"
    meta = materialize(spec, lab, force=True)
    return lab, spec, meta


def run_cmd(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def swarm_argv(lab: Path, meta: dict[str, Any], extra_args: list[str] | None = None, *, apply_run: bool = True) -> list[str]:
    run = meta.get("run") or {}
    argv = [
        str(BIN / "bead-swarm"),
        "--lab",
        "--seat",
        "grok",
        "--epic",
        meta["epic"],
        "--wave-size",
        str(run.get("wave_size") or 1),
        "--max-waves",
        str(run.get("max_waves") or 4),
        "--stagger-seconds",
        str(run.get("stagger_seconds") or 0),
        "--cwd",
        str(lab),
        "--probe-timeout",
        "20",
    ]
    if extra_args:
        argv.extend(extra_args)
    if apply_run and run.get("once"):
        argv.append("--once")
    if apply_run and run.get("no_am"):
        argv.append("--no-am")
    if apply_run and run.get("no_scavenge"):
        argv.append("--no-scavenge")
    if apply_run and run.get("hung_after") is not None:
        argv.extend(["--hung-after", str(run["hung_after"])])
    return argv


def run_swarm(
    lab: Path,
    env: dict[str, str],
    meta: dict[str, Any],
    extra_args: list[str] | None = None,
    *,
    apply_run: bool = True,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    return run_cmd(swarm_argv(lab, meta, extra_args, apply_run=apply_run), cwd=lab, env=env, timeout=timeout)


def start_swarm(
    lab: Path,
    env: dict[str, str],
    meta: dict[str, Any],
    extra_args: list[str] | None = None,
) -> subprocess.Popen[str]:
    log_path = lab / "tmp" / "bead-swarm" / "launcher-foreground.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "w")
    proc = subprocess.Popen(
        swarm_argv(lab, meta, extra_args),
        cwd=lab,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    proc._bead_swarm_log = handle  # type: ignore[attr-defined]
    return proc


def allowed_prompt(bead_ids: list[str]) -> str:
    lines = ["Allowed bead ids (this wave only):"]
    for bead_id in bead_ids:
        lines.append(f"- {bead_id}")
    lines.append("")
    lines.append("When you have finished this list, print WAVE_DONE and exit 0.")
    return "\n".join(lines) + "\n"


def worker_argv(
    lab: Path,
    bead_ids: list[str],
    *,
    wave: int = 1,
    abandon: bool = False,
    fault: str = "",
    hang_seconds: float | None = None,
) -> list[str]:
    prompt_path = lab / "tmp" / "bead-swarm" / f"manual-wave-{wave}.prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(allowed_prompt(bead_ids))
    argv = [
        str(BIN / "bead-swarm-lab-worker"),
        "--prompt-file",
        str(prompt_path),
        "--project",
        str(lab),
    ]
    if abandon:
        argv.append("--abandon")
    if fault:
        argv.extend(["--fault", fault])
    if hang_seconds is not None:
        argv.extend(["--hang-seconds", str(hang_seconds)])
    return argv


def run_worker(
    lab: Path,
    env: dict[str, str],
    bead_ids: list[str],
    *,
    wave: int = 1,
    abandon: bool = False,
    fault: str = "",
    hang_seconds: float | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    worker_env = dict(env)
    worker_env["BEAD_SWARM_WAVE"] = str(wave)
    argv = worker_argv(
        lab, bead_ids, wave=wave, abandon=abandon, fault=fault, hang_seconds=hang_seconds
    )
    return run_cmd(argv, cwd=lab, env=worker_env, timeout=timeout)


def start_worker(
    lab: Path,
    env: dict[str, str],
    bead_ids: list[str],
    *,
    wave: int = 1,
    abandon: bool = False,
    fault: str = "",
    hang_seconds: float | None = None,
) -> subprocess.Popen[str]:
    worker_env = dict(env)
    worker_env["BEAD_SWARM_WAVE"] = str(wave)
    argv = worker_argv(
        lab, bead_ids, wave=wave, abandon=abandon, fault=fault, hang_seconds=hang_seconds
    )
    log_path = lab / "tmp" / "bead-swarm" / f"manual-wave-{wave}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "w")
    proc = subprocess.Popen(
        argv,
        cwd=lab,
        env=worker_env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    proc._bead_swarm_log = handle  # type: ignore[attr-defined]
    return proc


def wait_until(pred, *, timeout: float = 8.0, interval: float = 0.05) -> bool:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def show_status(lab: Path, issue_id: str, env: dict[str, str]) -> str:
    shown = bd.show(lab, issue_id, env=env)
    return str(shown.get("status") or "")


def assert_expect_files(lab: Path, expect: dict[str, Any]) -> None:
    files = expect.get("files") or {}
    for rel, rules in files.items():
        path = lab / rel
        if not path.is_file():
            raise AssertionError(f"missing {rel}")
        text = path.read_text()
        if "line_count" in rules and len([line for line in text.splitlines() if line.strip()]) != int(rules["line_count"]):
            raise AssertionError(f"{rel} line_count want {rules['line_count']} got {text!r}")
        for needle in rules.get("contains") or []:
            if needle not in text:
                raise AssertionError(f"{rel} missing {needle!r} in {text!r}")
        if rules.get("startswith") and not text.startswith(str(rules["startswith"])):
            raise AssertionError(f"{rel} should start with {rules['startswith']!r}: {text!r}")
    if expect.get("file_count"):
        actual = len(list((lab / "files").glob("*.txt")))
        if actual != int(expect["file_count"]):
            raise AssertionError(f"file_count want {expect['file_count']} got {actual}")


def tmpdir() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory(prefix="bead-swarm-test-")
    original = tmp.cleanup

    def cleanup() -> None:
        try:
            original()
        except OSError:
            shutil.rmtree(tmp.name, ignore_errors=True)

    tmp.cleanup = cleanup  # type: ignore[method-assign]
    return tmp
