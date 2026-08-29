"""Agent Mail helpers. Exclusive conflicts often return exit 0 + JSON."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


def isolated_am_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{root / 'am.db'}"
    env["STORAGE_ROOT"] = str(root / "am-store")
    env["ALLOW_EPHEMERAL_PROJECTS_IN_DEFAULT_STORAGE"] = "1"
    env.setdefault("ASDF_RUBY_VERSION", "4.0.1")
    (root / "am-store").mkdir(parents=True, exist_ok=True)
    return env


def parse_reserve(stdout: str, stderr: str = "", returncode: int = 0) -> dict[str, Any]:
    blob = f"{stdout}\n{stderr}"
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    conflicts = []
    granted = []
    if isinstance(payload, dict):
        conflicts = list(payload.get("conflicts") or [])
        granted = list(payload.get("granted") or [])
    conflicted = bool(conflicts) or "FILE_RESERVATION_CONFLICT" in blob
    if returncode != 0 and "conflict" in blob.lower() and "activity lock" not in blob.lower():
        conflicted = True
    busy = "temporarily busy" in blob.lower() or "activity lock is busy" in blob.lower() or "database is locked" in blob.lower()
    return {
        "granted": granted,
        "conflicts": conflicts,
        "conflicted": conflicted,
        "busy": busy,
        "ok": (not conflicted) and (not busy) and returncode == 0,
        "raw": blob,
    }


def reserve(
    project: Path,
    agent: str,
    path: str,
    *,
    exclusive: bool,
    ttl: int,
    reason: str,
    env: dict[str, str],
    am_bin: str = "am",
) -> dict[str, Any]:
    argv = [
        am_bin,
        "file_reservations",
        "reserve",
        str(project),
        agent,
        path,
        "--ttl",
        str(ttl),
        "--reason",
        reason,
    ]
    argv.append("--exclusive" if exclusive else "--shared")
    result = subprocess.run(argv, cwd=project, text=True, capture_output=True, env=env)
    parsed = parse_reserve(result.stdout, result.stderr, result.returncode)
    parsed["returncode"] = result.returncode
    return parsed


def release(
    project: Path,
    agent: str,
    path: str,
    *,
    env: dict[str, str],
    am_bin: str = "am",
) -> None:
    subprocess.run(
        [
            am_bin,
            "file_reservations",
            "release",
            str(project),
            agent,
            "--paths",
            path,
        ],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )


def register_agent(
    project: Path,
    agent: str,
    *,
    env: dict[str, str],
    program: str = "bead-swarm-lab",
    model: str = "lab-worker",
    task: str = "lab-wave",
    am_bin: str = "am",
    attempts: int = 8,
) -> None:
    argv = [
        am_bin,
        "agents",
        "register",
        "--project",
        str(project),
        "--program",
        program,
        "--model",
        model,
        "--name",
        agent,
        "--task",
        task,
        "--json",
    ]
    last = ""
    for attempt in range(attempts):
        result = subprocess.run(argv, cwd=project, text=True, capture_output=True, env=env)
        if result.returncode == 0:
            return
        last = (result.stderr or result.stdout or "").strip()
        if "schema has changed" in last or "database is locked" in last.lower():
            time.sleep(0.05 * (attempt + 1))
            continue
        break
    raise SystemExit(f"am agents register failed: {last}")
