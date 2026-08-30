"""Classify an empty epic frontier: graph-ready vs blocked vs semantic gates."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from beadswarm.contract import missing_claim_requires, parse_contract

BLOCKING_DEP_TYPES = frozenset({"blocks", "blocked_by"})


@dataclass
class Diagnosis:
    epic_id: str
    skipped_claim_requires: list[tuple[str, list[str]]] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    in_progress: list[str] = field(default_factory=list)
    assigned_hidden: list[str] = field(default_factory=list)
    open_unblocked: list[str] = field(default_factory=list)
    needs_investigation: bool = False


def _assignee(item: dict[str, Any]) -> str:
    return str(item.get("assignee") or "").strip()


def _status(item: dict[str, Any]) -> str:
    return str(item.get("status") or "").strip().lower()


def blocking_ids(item: dict[str, Any]) -> list[str]:
    """Issue ids that block this bead (`blocks` / `blocked_by`, not parent-child)."""
    found: list[str] = []
    seen: set[str] = set()

    def add(bead_id: str) -> None:
        if bead_id and bead_id not in seen:
            seen.add(bead_id)
            found.append(bead_id)

    for dep in item.get("dependencies") or []:
        if not isinstance(dep, dict):
            continue
        dtype = str(dep.get("dependency_type") or dep.get("type") or "").lower()
        if dtype not in BLOCKING_DEP_TYPES:
            continue
        add(str(dep.get("id") or dep.get("depends_on_id") or ""))
    for raw in item.get("blocked_by") or []:
        if isinstance(raw, dict):
            add(str(raw.get("id") or raw.get("depends_on_id") or ""))
        else:
            add(str(raw))
    return found


def unresolved_blockers(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[str]:
    """Blockers that are missing or not closed."""
    open_ids: list[str] = []
    dep_status: dict[str, str] = {}
    for dep in item.get("dependencies") or []:
        if not isinstance(dep, dict):
            continue
        dtype = str(dep.get("dependency_type") or dep.get("type") or "").lower()
        if dtype not in BLOCKING_DEP_TYPES:
            continue
        bid = str(dep.get("id") or dep.get("depends_on_id") or "")
        if bid and dep.get("status"):
            dep_status[bid] = str(dep.get("status") or "")
    for raw in item.get("blocked_by") or []:
        if isinstance(raw, dict):
            bid = str(raw.get("id") or raw.get("depends_on_id") or "")
            if bid and raw.get("status"):
                dep_status[bid] = str(raw.get("status") or "")
    for bid in blocking_ids(item):
        blocker = by_id.get(bid)
        status = ""
        if blocker:
            status = _status(blocker)
        elif bid in dep_status:
            status = dep_status[bid].strip().lower()
        if status != "closed":
            open_ids.append(bid)
    return open_ids


def classify(
    epic_id: str,
    items: list[dict[str, Any]],
    *,
    project: Path | str,
    graph_ready_ids: set[str] | None = None,
) -> Diagnosis:
    """Bucket open non-epic descendants. Investigation is semantic gates, not graph blocks."""
    ready_ids = graph_ready_ids or set()
    by_id = {str(item["id"]): item for item in items if item.get("id")}
    skipped: list[tuple[str, list[str]]] = []
    blocked: list[str] = []
    in_progress: list[str] = []
    assigned_hidden: list[str] = []
    open_unblocked: list[str] = []

    for item in items:
        bead_id = str(item.get("id") or "")
        if not bead_id or item.get("issue_type") == "epic":
            continue
        status = _status(item)
        if status == "closed":
            continue
        if status == "in_progress":
            in_progress.append(bead_id)
            continue
        missing = missing_claim_requires(project, parse_contract(item))
        if bead_id in ready_ids and missing:
            skipped.append((bead_id, missing))
            continue
        if status == "open" and unresolved_blockers(item, by_id):
            blocked.append(bead_id)
            continue
        if status == "open" and _assignee(item):
            assigned_hidden.append(bead_id)
            continue
        open_unblocked.append(bead_id)

    return Diagnosis(
        epic_id=epic_id,
        skipped_claim_requires=skipped,
        blocked=blocked,
        in_progress=in_progress,
        assigned_hidden=assigned_hidden,
        open_unblocked=open_unblocked,
        needs_investigation=bool(skipped),
    )


def diagnose_epic(
    epic_id: str,
    *,
    project: Path | str,
    descendant_ids: set[str],
    graph_ready_ids: set[str],
    show: Callable[[str], dict[str, Any] | None],
    extra_items: list[dict[str, Any]] | None = None,
) -> Diagnosis:
    """Load descendant shows (plus any extra ready rows) and classify."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for did in sorted(descendant_ids):
        shown = show(did)
        if shown and str(shown.get("id") or "") == did:
            items.append(shown)
            seen.add(did)
    for item in extra_items or []:
        iid = str(item.get("id") or "")
        if iid and iid not in seen:
            items.append(item)
            seen.add(iid)
    return classify(epic_id, items, project=project, graph_ready_ids=graph_ready_ids)


def _csv(ids: list[str]) -> str:
    return ", ".join(ids) if ids else "(none)"


def format_diagnosis(diag: Diagnosis) -> str:
    skipped = "; ".join(
        f"{bead_id} missing {', '.join(paths)}" for bead_id, paths in diag.skipped_claim_requires
    ) or "(none)"
    return "\n".join(
        [
            f"diagnose: empty frontier under {diag.epic_id}",
            f"  skipped-claim_requires: {skipped}",
            f"  blocked: {_csv(diag.blocked)}",
            f"  in_progress: {_csv(diag.in_progress)}",
            f"  assigned-hidden: {_csv(diag.assigned_hidden)}",
            f"  open-unblocked: {_csv(diag.open_unblocked)}",
            f"  needs_investigation: {'yes' if diag.needs_investigation else 'no'}",
        ]
    )
