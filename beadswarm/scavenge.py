"""Classify in_progress beads: complete oracle, incomplete corpse, or live hang."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from beadswarm import amutil, bd, events, heartbeat
from beadswarm.contract import parse_contract

COMPLETE = "complete"
INCOMPLETE = "incomplete"
LIVE = "live"
HUNG = "hung"
UNKNOWN = "unknown"


def oracle_complete(project: Path, bead_id: str, contract: dict[str, Any]) -> bool:
    files = list(contract.get("files") or [])
    if not files:
        return False
    payload = str(contract.get("payload") or bead_id)
    for rel in files:
        path = project / rel
        if not path.is_file():
            return False
        text = path.read_text()
        if payload not in text and bead_id not in text:
            return False
    return True


def classify(
    project: Path,
    bead_id: str,
    *,
    max_age: float = 2.0,
    hung_after: float | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    shown = bd.show(project, bead_id, env=env)
    contract = parse_contract(shown)
    complete = oracle_complete(project, bead_id, contract)
    hb = heartbeat.read(project, bead_id)
    if not hb:
        kind = UNKNOWN
        is_live = False
    else:
        is_live = heartbeat.live(project, bead_id, max_age=max_age)
        stale = hung_after is not None and not heartbeat.fresh(project, bead_id, max_age=hung_after)
        if is_live and stale:
            kind = HUNG
        elif is_live and not complete:
            kind = LIVE
        elif complete:
            kind = COMPLETE
        else:
            kind = INCOMPLETE
    return {
        "bead": bead_id,
        "kind": kind,
        "complete": complete,
        "live": is_live,
        "agent": (hb or {}).get("agent") or "",
        "pid": (hb or {}).get("pid"),
        "wave_pid": (hb or {}).get("wave_pid"),
        "contract": contract,
        "status": shown.get("status"),
    }


def in_progress_ids(project: Path, env: dict[str, str] | None = None) -> list[str]:
    stdout = bd.run(
        [bd.bd_bin(env), "list", "--status", "in_progress", "--json", "--limit", "0"],
        project,
        env=env,
    )
    return [
        str(item["id"])
        for item in bd.parse_issues(stdout)
        if item.get("id") and item.get("issue_type") != "epic"
    ]


def _env() -> dict[str, str]:
    return os.environ.copy()


def apply(
    project: Path,
    report: dict[str, Any],
    *,
    steal_hung: bool = False,
    env: dict[str, str] | None = None,
) -> str:
    """Mutate graph/AM to match the classification. Returns the action taken."""
    kind = report["kind"]
    bead_id = report["bead"]
    agent = str(report.get("agent") or "BlueLake")
    merged = _env()
    if env:
        merged.update(env)
    am_bin = merged.get("AM_BIN") or merged.get("BEAD_SWARM_AM_BIN") or "am"
    if kind == UNKNOWN:
        events.emit(project, "scavenge-skip-unknown", bead=bead_id)
        return "skip-unknown"
    if kind == LIVE or (kind == HUNG and not steal_hung):
        events.emit(project, "scavenge-skip-live", bead=bead_id, agent=agent)
        return "skip-live"
    if kind == COMPLETE:
        bd.run(
            [
                bd.bd_bin(merged),
                "close",
                bead_id,
                "--reason",
                "scavenge: file oracle complete, agent never closed",
                "--json",
            ],
            project,
            env=merged,
        )
        for rel in report["contract"].get("files") or []:
            amutil.release(project, agent, rel, env=merged, am_bin=am_bin)
        events.emit(project, "scavenge-close", bead=bead_id)
        return "close"
    bd.run(
        [bd.bd_bin(merged), "update", bead_id, "--status", "open", "--assignee", "", "--json"],
        project,
        env=merged,
    )
    for rel in report["contract"].get("files") or []:
        amutil.release(project, agent, rel, env=merged, am_bin=am_bin)
    events.emit(project, "scavenge-requeue", bead=bead_id, agent=agent)
    return "requeue"


def scavenge(
    project: Path,
    *,
    max_age: float = 2.0,
    hung_after: float | None = None,
    steal_hung: bool = False,
    env: dict[str, str] | None = None,
    allowed_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    steal = steal_hung or hung_after is not None
    out = []
    for bead_id in in_progress_ids(project, env=env):
        if allowed_ids is not None and bead_id not in allowed_ids:
            continue
        report = classify(
            project, bead_id, max_age=max_age, hung_after=hung_after, env=env
        )
        report["action"] = apply(project, report, steal_hung=steal, env=env)
        out.append(report)
    return out
