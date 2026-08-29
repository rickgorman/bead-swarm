from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def bd_bin() -> str:
    return os.environ.get("BD_BIN") or os.environ.get("BEAD_SWARM_BR_BIN") or os.environ.get("BR_BIN") or "bd"


def run(argv: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged.setdefault("ASDF_RUBY_VERSION", "4.0.1")
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, env=merged)
    if result.returncode != 0:
        raise SystemExit(f"$ {' '.join(argv)}\n{result.stderr or result.stdout}")
    return result.stdout


def parse_issues(stdout: str) -> list[dict[str, Any]]:
    payload = json.loads(stdout or "[]")
    if isinstance(payload, dict) and "issues" in payload:
        payload = payload["issues"]
    if isinstance(payload, dict) and payload.get("id"):
        return [payload]
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def create(
    cwd: Path,
    title: str,
    description: str,
    *,
    parent: str | None = None,
    issue_type: str = "task",
    labels: list[str] | None = None,
    priority: str = "1",
    env: dict[str, str] | None = None,
) -> str:
    argv = [
        bd_bin(),
        "create",
        "-t",
        issue_type,
        "-p",
        priority,
        "--json",
        "--description",
        description,
        title,
    ]
    if parent:
        argv.extend(["--parent", parent])
    if labels:
        argv.extend(["--labels", ",".join(labels)])
    payload = parse_issues(run(argv, cwd, env=env))
    bead_id = payload[0].get("id") if payload else None
    if not bead_id:
        raise SystemExit(f"bd create returned no id: {payload}")
    return str(bead_id)


def dep_add(cwd: Path, blocked: str, blocker: str, env: dict[str, str] | None = None) -> None:
    run([bd_bin(), "dep", "add", blocked, blocker], cwd, env=env)


def ready(cwd: Path, env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    stdout = run([bd_bin(), "ready", "--json", "--limit", "200"], cwd, env=env)
    return [item for item in parse_issues(stdout) if item.get("issue_type") != "epic"]


def show(cwd: Path, issue_id: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    payload = parse_issues(run([bd_bin(), "show", issue_id, "--json"], cwd, env=env))
    return payload[0] if payload else {}
