from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def events_path(project: Path) -> Path:
    path = project / "tmp" / "bead-swarm" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def emit(project: Path, event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    path = events_path(project)
    with path.open("a") as handle:
        handle.write(json.dumps(payload) + "\n")


def load(project: Path) -> list[dict[str, Any]]:
    path = events_path(project)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def count(project: Path, event: str) -> int:
    return sum(1 for row in load(project) if row.get("event") == event)
