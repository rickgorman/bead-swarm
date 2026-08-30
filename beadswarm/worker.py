"""Deterministic lab worker: claim, AM-lease, write, release, close."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from beadswarm import amutil, events, heartbeat
from beadswarm.contract import parse_contract

AGENT_NAMES = ("BlueLake", "CoralPeak", "JadeFox", "IvoryOwl", "AmberFox", "ScarletCave")


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("ASDF_RUBY_VERSION", "4.0.1")
    return env


def br_bin() -> str:
    return os.environ.get("BR_BIN") or os.environ.get("BEAD_SWARM_BR_BIN") or "br"


def am_bin() -> str:
    return os.environ.get("AM_BIN") or os.environ.get("BEAD_SWARM_AM_BIN") or "am"


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, env=command_env())
    if result.returncode != 0:
        raise SystemExit(f"$ {' '.join(argv)}\n{result.stderr or result.stdout}")
    return result


def parse_allowed(prompt: str) -> list[str]:
    ids: list[str] = []
    in_list = False
    for line in prompt.splitlines():
        if line.startswith("Allowed bead ids"):
            in_list = True
            continue
        if in_list:
            if line.startswith("- "):
                token = line[2:].strip()
                if token and token != "(none)":
                    ids.append(token)
            elif line.strip() == "":
                continue
            else:
                break
    return ids


def show_bead(project: Path, bead_id: str) -> dict[str, Any]:
    payload = json.loads(run([br_bin(), "show", bead_id, "--json"], project).stdout or "{}")
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return {}
    return payload


def apply_mode(project: Path, bead_id: str, contract: dict[str, Any]) -> list[Path]:
    written: list[Path] = []
    payload = contract.get("payload") or bead_id
    mode = contract["mode"]
    if mode == "rmw-append":
        gate = contract.get("gate")
        if gate:
            deadline = time.time() + 10
            while time.time() < deadline and not (project / str(gate)).exists():
                time.sleep(0.02)
        hold = float(contract.get("hold_seconds") or 0)
        for rel in contract["files"]:
            path = project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            current = path.read_text() if path.is_file() else ""
            if hold:
                time.sleep(hold)
            line = f"{payload} {bead_id} {rel}\n"
            path.write_text(current + line)
            written.append(path)
        contract["hold_seconds"] = 0
        return written
    if mode in ("write", "append"):
        for rel in contract["files"]:
            path = project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            line = f"{payload} {bead_id} {rel}\n"
            if mode == "append":
                with path.open("a") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
            else:
                path.write_text(line)
            written.append(path)
        return written
    if mode == "copy":
        source = contract.get("source")
        if not source:
            raise SystemExit("copy mode needs source")
        src_text = (project / source).read_text()
        for rel in contract["files"]:
            path = project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(src_text + f"{payload} {bead_id} copied {source} -> {rel}\n")
            written.append(path)
        return written
    if mode == "merge":
        chunks = []
        for source in contract.get("sources") or []:
            src_path = project / source
            chunks.append(src_path.read_text() if src_path.is_file() else f"MISSING {source}\n")
        for rel in contract["files"]:
            path = project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(chunks) + f"{payload} {bead_id} merged -> {rel}\n")
            written.append(path)
        return written
    raise SystemExit(f"unknown mode {mode}")


def reserve_with_retry(
    project: Path,
    agent: str,
    rel: str,
    contract: dict[str, Any],
    bead_id: str,
) -> None:
    if contract["lease"] == "none":
        return
    exclusive = contract["lease"] != "shared"
    deadline = time.time() + float(os.environ.get("BEAD_SWARM_RESERVE_SECONDS") or "45")
    while True:
        parsed = amutil.reserve(
            project,
            agent,
            rel,
            exclusive=exclusive,
            ttl=int(contract["ttl"]),
            reason=bead_id,
            env=command_env(),
            am_bin=am_bin(),
        )
        if parsed["ok"]:
            events.emit(project, "am-granted", bead=bead_id, path=rel, agent=agent)
            return
        if parsed["conflicted"] or parsed.get("busy"):
            holder = ""
            if parsed["conflicts"]:
                holder = str(parsed["conflicts"][0].get("holder") or "")
            kind = "am-busy" if parsed.get("busy") and not parsed["conflicted"] else "am-conflict"
            events.emit(project, kind, bead=bead_id, path=rel, agent=agent, holder=holder)
            print(f"{kind} {rel} holder={holder}", flush=True)
            if time.time() >= deadline:
                raise SystemExit(f"am reserve timeout on {rel} for {bead_id}")
            time.sleep(0.12)
            continue
        raise SystemExit(f"am reserve failed: {parsed['raw']}")


def die_now() -> None:
    os.kill(os.getpid(), signal.SIGKILL)


def bv_next(cwd: Path) -> str | None:
    if os.environ.get("BEAD_SWARM_SKIP_BV", "1") == "1":
        return None
    env = command_env()
    env.setdefault("BV_ROBOT_NOT_READY_LABELS", "verify:recon")
    # Never bare `bv` — that launches a TUI. Robot wrapper only.
    for argv in (
        ["bv-robot-next", "-f", "json"],
        ["bv", "--robot-next", "-f", "json", "--no-cache"],
    ):
        try:
            result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, env=env)
        except OSError:
            continue
        if result.returncode != 0 or not result.stdout.strip():
            continue
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("actionable") is False:
            return None
        if isinstance(payload, dict) and payload.get("id"):
            return str(payload["id"])
    return None


def hang_loop(project: Path, bead_id: str, agent: str, contract: dict[str, Any]) -> None:
    deadline = time.time() + float(contract.get("hang_seconds") or 3600)
    while time.time() < deadline:
        if contract.get("heartbeat", True):
            heartbeat.write(project, bead_id, agent)
        time.sleep(0.08)


def process_bead(project: Path, agent: str, bead_id: str, *, abandon: bool, fault: str) -> None:
    shown = show_bead(project, bead_id)
    contract = parse_contract(shown)
    if fault:
        contract["fault"] = fault
    fault = str(contract.get("fault") or "")
    if abandon:
        contract["close"] = False
        contract["release"] = False
        contract["hold_seconds"] = contract["hold_seconds"] or 0.4
        contract["ttl"] = min(int(contract["ttl"]), 2)
    if fault == "skip-close":
        contract["close"] = False
    if fault == "skip-release":
        contract["release"] = False
    if fault == "no-heartbeat":
        contract["heartbeat"] = False
    if os.environ.get("BEAD_SWARM_LAB_HANG_SECONDS"):
        contract["hang_seconds"] = float(os.environ["BEAD_SWARM_LAB_HANG_SECONDS"])

    heartbeat.write(project, bead_id, agent)

    leases: list[tuple[str, str]] = []
    for rel in contract["files"]:
        leases.append((rel, contract["lease"]))
    if contract.get("source"):
        leases.append((str(contract["source"]), "shared"))
    for source in contract.get("sources") or []:
        leases.append((str(source), "shared"))

    seen_paths: set[str] = set()
    unique_leases: list[tuple[str, str]] = []
    for rel, kind in leases:
        key = f"{kind}:{rel}"
        if key in seen_paths:
            continue
        seen_paths.add(key)
        unique_leases.append((rel, kind))

    for rel, kind in unique_leases:
        if kind == "none":
            continue
        tagged = dict(contract)
        tagged["lease"] = kind
        reserve_with_retry(project, agent, rel, tagged, bead_id)

    if fault == "die-after-reserve":
        die_now()
    if fault in ("hang-heartbeat", "hang", "hang-no-heartbeat"):
        if fault == "hang-no-heartbeat":
            contract["heartbeat"] = False
        hang_loop(project, bead_id, agent, contract)
        events.emit(project, "held", bead=bead_id, files=[])
        return
    if fault == "hang-then-succeed":
        hang_loop(project, bead_id, agent, contract)
    if fault == "skip-write":
        events.emit(project, "held", bead=bead_id, files=[])
        return
    if fault == "eacces-write":
        files_dir = project / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(files_dir, 0o555)
        try:
            apply_mode(project, bead_id, contract)
        except OSError as exc:
            events.emit(project, "eacces", bead=bead_id, error=str(exc))
            raise SystemExit(f"eacces-write: {exc}")
        finally:
            os.chmod(files_dir, 0o755)
        raise SystemExit("eacces-write did not fail")

    written = apply_mode(project, bead_id, contract)
    if fault == "die-after-write":
        die_now()
    if contract["hold_seconds"]:
        if contract.get("heartbeat", True):
            heartbeat.write(project, bead_id, agent)
        time.sleep(contract["hold_seconds"])

    if contract["release"]:
        for rel, kind in unique_leases:
            if kind == "none":
                continue
            amutil.release(project, agent, rel, env=command_env(), am_bin=am_bin())
            events.emit(project, "am-release", bead=bead_id, path=rel, agent=agent)

    rels = [str(path.relative_to(project)) for path in written]
    if not contract["close"]:
        events.emit(project, "held", bead=bead_id, files=rels)
        print(f"held {bead_id} -> {', '.join(rels)}", flush=True)
        return

    close_argv = [br_bin(), "close", bead_id, "--actor", agent, "--reason", f"wrote {', '.join(rels)}", "--json"]
    run(close_argv, project)
    if fault == "close-twice":
        again = subprocess.run(close_argv, cwd=project, text=True, capture_output=True, env=command_env())
        blob = f"{again.stdout}\n{again.stderr}".lower()
        if again.returncode != 0 and "already" not in blob and "closed" not in blob:
            raise SystemExit(f"second close failed: {blob.strip()}")
        events.emit(project, "close-twice", bead=bead_id, rc=again.returncode)
    events.emit(project, "closed", bead=bead_id, files=rels)
    print(f"closed {bead_id} -> {', '.join(rels)}", flush=True)
    if fault == "die-after-close":
        die_now()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file")
    parser.add_argument("--project", default=os.getcwd())
    parser.add_argument("--abandon", action="store_true")
    parser.add_argument("--fault", default=os.environ.get("BEAD_SWARM_LAB_FAULT", ""))
    parser.add_argument("--hang-seconds", type=float, default=None)
    parser.add_argument("prompt", nargs="?", default="")
    args, _unknown = parser.parse_known_args(argv)
    project = Path(args.project).resolve()
    prompt = Path(args.prompt_file).read_text() if args.prompt_file else (args.prompt or "")
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read()
    allowed = parse_allowed(prompt)
    if not allowed:
        raise SystemExit("lab-worker: no allowed bead ids in prompt")

    wave_n = int(os.environ.get("BEAD_SWARM_WAVE") or "1")
    agent = AGENT_NAMES[(wave_n - 1) % len(AGENT_NAMES)]
    amutil.register_agent(project, agent, env=command_env(), am_bin=am_bin())

    remaining = list(allowed)
    while remaining:
        pick = bv_next(project)
        if pick and pick not in remaining:
            events.emit(project, "off-epic-bv", pick=pick, allowed=list(remaining))
            print(f"off-epic-bv: {pick}", flush=True)
        bead_id = pick if pick in remaining else remaining[0]
        claim = subprocess.run(
            [br_bin(), "update", bead_id, "--claim", "--actor", agent, "--json"],
            cwd=project,
            text=True,
            capture_output=True,
            env=command_env(),
        )
        blob = f"{claim.stdout}\n{claim.stderr}"
        claimed_by_other = "already claimed" in blob.lower()
        if claim.returncode != 0 or claimed_by_other:
            events.emit(project, "claim-miss", bead=bead_id, agent=agent, detail=blob.strip()[:300])
            print(f"claim miss {bead_id}: {blob.strip()}", flush=True)
            remaining.remove(bead_id)
            continue
        events.emit(project, "claimed", bead=bead_id, agent=agent)
        if args.fault == "die-after-claim":
            die_now()
        contract_hang = {}
        if args.hang_seconds is not None:
            os.environ["BEAD_SWARM_LAB_HANG_SECONDS"] = str(args.hang_seconds)
        process_bead(project, agent, bead_id, abandon=args.abandon, fault=args.fault)
        remaining.remove(bead_id)
        if args.abandon or args.fault.startswith("hang"):
            break

    print("WAVE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
