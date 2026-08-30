"""Materialize a scenario JSON into an isolated git+bd lab directory."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from beadswarm import bd
from beadswarm.contract import render
from beadswarm.home import swarm_home

REPO_ROOT = swarm_home()
SCENARIOS = REPO_ROOT / "scenarios"


def load_scenario(name: str) -> dict[str, Any]:
    path = Path(name)
    if not path.is_file():
        candidate = SCENARIOS / name
        if candidate.with_suffix(".json").is_file():
            path = candidate.with_suffix(".json")
        elif candidate.is_file():
            path = candidate
        else:
            matches = sorted(SCENARIOS.glob(f"*{name}*.json"))
            if len(matches) == 1:
                path = matches[0]
            elif not matches:
                raise SystemExit(f"scenario not found: {name}")
            else:
                raise SystemExit(f"ambiguous scenario {name}: {', '.join(p.name for p in matches)}")
    data = json.loads(path.read_text())
    data["_path"] = str(path)
    data["_id"] = data.get("id") or path.stem
    return expand(data)


def expand(spec: dict[str, Any]) -> dict[str, Any]:
    generate = spec.get("generate") or {}
    if generate.get("kind") == "layered":
        count = int(generate.get("count") or 20)
        width = int(generate.get("width") or 6)
        beads = []
        deps = []
        for n in range(1, count + 1):
            name = f"{n:02d}"
            key = f"b{name}"
            beads.append(
                {
                    "key": key,
                    "title": f"[lab] write files/{name}.txt",
                    "files": [f"files/{name}.txt"],
                    "mode": "write",
                    "lease": "exclusive",
                    "payload": key,
                }
            )
        for i in range(width, count):
            blocked = f"b{i + 1:02d}"
            if i < 18:
                deps.append([blocked, f"b{i - width + 1:02d}"])
            elif i == 18:
                for j in range(13, 16):
                    deps.append([blocked, f"b{j:02d}"])
            else:
                for j in range(16, 20):
                    deps.append([blocked, f"b{j:02d}"])
        spec = dict(spec)
        spec["beads"] = beads
        spec["deps"] = deps
        spec.setdefault("epic", {"title": f"[lab] {count} text files, width {width}"})
        expect = dict(spec.get("expect") or {})
        expect.setdefault("ready_width", width)
        expect.setdefault("file_count", count)
        spec["expect"] = expect
    return spec


def bead_body(bead: dict[str, Any]) -> str:
    contract = {
        "files": list(bead.get("files") or []),
        "mode": bead.get("mode") or "write",
        "lease": bead.get("lease") or "exclusive",
        "payload": bead.get("payload") or bead.get("key") or "",
        "ttl": bead.get("ttl", 60),
        "release": bead.get("release", True),
        "close": bead.get("close", True),
        "hold_seconds": bead.get("hold_seconds") or 0,
        "fault": bead.get("fault") or "",
        "heartbeat": bead.get("heartbeat", True),
        "hang_seconds": bead.get("hang_seconds") or 0,
        "gate": bead.get("gate") or "",
    }
    if bead.get("source"):
        contract["source"] = bead["source"]
    if bead.get("sources"):
        contract["sources"] = list(bead["sources"])
    if bead.get("claim_requires"):
        contract["claim_requires"] = list(bead["claim_requires"])
    background = bead.get("body") or f"Lab bead `{bead.get('key')}`."
    return f"{background}\n\n{render(contract)}"


def flatten_beads(spec: dict[str, Any]) -> list[dict[str, Any]]:
    beads = list(spec.get("beads") or [])
    for slice_epic in spec.get("slices") or []:
        for bead in slice_epic.get("beads") or []:
            item = dict(bead)
            item["_slice"] = slice_epic["key"]
            beads.append(item)
    return beads


def materialize(spec: dict[str, Any], dest: Path, *, force: bool = False) -> dict[str, Any]:
    if dest.exists() and force:
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "files").mkdir(exist_ok=True)
    (dest / "tmp" / "bead-swarm").mkdir(parents=True, exist_ok=True)

    title = (spec.get("epic") or {}).get("title") or spec.get("title") or spec["_id"]
    (dest / "AGENTS.md").write_text(
        f"# bead-swarm lab — {spec['_id']}\n\n{spec.get('title') or ''}\n\n{spec.get('description') or ''}\n"
    )

    if not (dest / ".git").exists():
        bd.run(["git", "init", "-q"], dest)
        bd.run(["git", "config", "user.email", "lab@bead-swarm.local"], dest)
        bd.run(["git", "config", "user.name", "bead-swarm-lab"], dest)

    prefix = spec.get("prefix") or "lab"
    if not (dest / ".beads").exists():
        bd.run(["bd", "init", "--backend", "sqlite", "--prefix", prefix, "--skip-hooks", "-q"], dest)

    epic_id = bd.create(
        dest,
        title,
        spec.get("description") or (spec.get("epic") or {}).get("body") or spec.get("title") or title,
        issue_type="epic",
    )

    keymap: dict[str, str] = {"epic": epic_id}
    slice_ids: dict[str, str] = {}
    for slice_epic in spec.get("slices") or []:
        slice_id = bd.create(
            dest,
            slice_epic.get("title") or slice_epic["key"],
            slice_epic.get("body") or f"Slice {slice_epic['key']}",
            parent=epic_id,
            issue_type="epic",
        )
        slice_ids[slice_epic["key"]] = slice_id
        keymap[slice_epic["key"]] = slice_id

    for bead in flatten_beads(spec):
        parent = slice_ids.get(bead.get("_slice") or "") or epic_id
        bead_id = bd.create(
            dest,
            bead.get("title") or f"[lab] {bead['key']}",
            bead_body(bead),
            parent=parent,
            labels=list(bead.get("labels") or []),
            priority=str(bead.get("priority") or "1"),
        )
        keymap[bead["key"]] = bead_id

    for extra in spec.get("extra_epics") or []:
        extra_id = bd.create(
            dest,
            extra.get("title") or extra["key"],
            extra.get("body") or f"Extra epic {extra['key']}",
            issue_type="epic",
        )
        keymap[extra["key"]] = extra_id
        for bead in extra.get("beads") or []:
            bead_id = bd.create(
                dest,
                bead.get("title") or f"[lab] {bead['key']}",
                bead_body(bead),
                parent=extra_id,
                labels=list(bead.get("labels") or []),
                priority=str(bead.get("priority") or "1"),
            )
            keymap[bead["key"]] = bead_id

    for rel, content in (spec.get("seed_files") or {}).items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    for blocked_key, blocker_key in spec.get("deps") or []:
        if blocked_key not in keymap or blocker_key not in keymap:
            raise SystemExit(f"dep references unknown key: {blocked_key} <- {blocker_key}")
        bd.dep_add(dest, keymap[blocked_key], keymap[blocker_key])

    if spec.get("cycle"):
        a, b = spec["cycle"]
        try:
            bd.dep_add(dest, keymap[a], keymap[b])
        except SystemExit as exc:
            keymap["_cycle_error"] = str(exc)

    for left, right in spec.get("relates") or []:
        bd.run([bd.bd_bin(), "dep", "relate", keymap[left], keymap[right]], dest)

    ready_items = bd.ready(dest)
    ready_ids = [item["id"] for item in ready_items]
    expect_width = (spec.get("expect") or {}).get("ready_width")
    if expect_width is not None and len(ready_ids) != int(expect_width):
        # Still write artifacts so the test can show the graph.
        pass

    (dest / "EPIC").write_text(epic_id + "\n")
    meta = {
        "scenario": spec["_id"],
        "epic": epic_id,
        "keymap": keymap,
        "ready": ready_ids,
        "ready_width": len(ready_ids),
        "slices": slice_ids,
        "expect": spec.get("expect") or {},
        "run": spec.get("run") or {},
        "harness": spec.get("harness") or "swarm",
    }
    (dest / "SCENARIO.json").write_text(json.dumps(meta, indent=2) + "\n")
    (dest / "KEYMAP.json").write_text(json.dumps(keymap, indent=2) + "\n")
    return meta


def list_scenarios() -> list[Path]:
    return sorted(SCENARIOS.glob("*.json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize a bead-swarm lab scenario")
    parser.add_argument("--scenario", default="00-width6")
    parser.add_argument("--dir", default="/tmp/bead-swarm-lab")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--list", action="store_true", help="Print scenario ids and titles")
    args = parser.parse_args(argv)
    if args.list:
        for path in list_scenarios():
            data = json.loads(path.read_text())
            print(f"{path.stem}  {data.get('id', path.stem)}  {data.get('title', '')}")
        return 0
    spec = load_scenario(args.scenario)
    dest = Path(args.dir).resolve()
    meta = materialize(spec, dest, force=args.force)
    print(f"lab: {dest}")
    print(f"scenario: {meta['scenario']}")
    print(f"epic: {meta['epic']}")
    print(f"ready width: {meta['ready_width']}")
    print("ready: " + ", ".join(meta["ready"]))
    expect = meta["expect"]
    if expect.get("ready_width") is not None and meta["ready_width"] != int(expect["ready_width"]):
        print(
            f"warning: ready width {meta['ready_width']} != {expect['ready_width']}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
