"""On-disk liveness for a claimed bead: pid + optional heartbeat."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _dir(project: Path) -> Path:
    path = project / "tmp" / "bead-swarm" / "heartbeats"
    path.mkdir(parents=True, exist_ok=True)
    return path


def path_for(project: Path, bead_id: str) -> Path:
    return _dir(project) / f"{bead_id}.json"


def write(project: Path, bead_id: str, agent: str) -> None:
    payload = {
        "bead": bead_id,
        "agent": agent,
        "pid": os.getpid(),
        "ts": time.time(),
    }
    path_for(project, bead_id).write_text(json.dumps(payload) + "\n")


def read(project: Path, bead_id: str) -> dict[str, Any] | None:
    path = path_for(project, bead_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def fresh(project: Path, bead_id: str, *, max_age: float) -> bool:
    data = read(project, bead_id)
    if not data:
        return False
    try:
        ts = float(data.get("ts") or 0)
    except (TypeError, ValueError):
        return False
    return (time.time() - ts) <= max_age


def live(project: Path, bead_id: str, *, max_age: float) -> bool:
    """A bead is live if its recorded pid still exists.

    A fresh heartbeat with a dead pid is a crash, not a hang.
    Heartbeat-only (no pid) uses max_age as a fallback.
    """
    data = read(project, bead_id)
    if not data:
        return False
    try:
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid:
        return pid_alive(pid)
    return fresh(project, bead_id, max_age=max_age)
