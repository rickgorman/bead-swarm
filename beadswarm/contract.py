from __future__ import annotations

import json
import re
from typing import Any

FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
TITLE_FILE = re.compile(r"(files/\S+\.\w+)")


def parse_contract(shown: dict[str, Any]) -> dict[str, Any]:
    desc = shown.get("description") or shown.get("body") or ""
    match = FENCE.search(desc)
    if match:
        data = json.loads(match.group(1))
        if not isinstance(data, dict):
            raise SystemExit("lab contract JSON must be an object")
        return normalize(data, shown)

    title = shown.get("title") or ""
    name = TITLE_FILE.search(title)
    files = [name.group(1)] if name else []
    return normalize({"files": files, "mode": "write", "lease": "exclusive"}, shown)


def normalize(data: dict[str, Any], shown: dict[str, Any] | None = None) -> dict[str, Any]:
    files = list(data.get("files") or [])
    labels = []
    if shown:
        raw_labels = shown.get("labels") or []
        if isinstance(raw_labels, str):
            raw_labels = [part.strip() for part in raw_labels.split(",") if part.strip()]
        labels = list(raw_labels)
    for label in labels:
        if isinstance(label, str) and label.startswith("touch:"):
            path = label.split(":", 1)[1]
            if path and path not in files:
                files.append(path)
    lease = data.get("lease") or "exclusive"
    if lease not in ("exclusive", "shared", "none"):
        raise SystemExit(f"unknown lease {lease}")
    mode = data.get("mode") or "write"
    return {
        "files": files,
        "mode": mode,
        "lease": lease,
        "payload": data.get("payload") or "",
        "source": data.get("source"),
        "sources": list(data.get("sources") or []),
        "ttl": int(data.get("ttl") or 60),
        "release": bool(data.get("release", True)),
        "close": bool(data.get("close", True)),
        "hold_seconds": float(data.get("hold_seconds") or 0),
        "labels": labels,
        "fault": str(data.get("fault") or ""),
        "heartbeat": bool(data.get("heartbeat", True)),
        "hang_seconds": float(data.get("hang_seconds") or 0),
        "gate": data.get("gate") or "",
    }


def render(data: dict[str, Any]) -> str:
    body = json.dumps(data, indent=2)
    return f"## Lab contract\n\n```json\n{body}\n```\n"
