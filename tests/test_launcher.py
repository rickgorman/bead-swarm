from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from subprocess import CompletedProcess

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import ROOT, isolated_env, run_cmd, tmpdir, write_executable

SWARM = ROOT / "bin" / "bead-swarm"

FAKE_BR = r'''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
positional = [a for a in args[1:] if not a.startswith("-")] if args else []
epic = {"id": "leverage-epic-1", "title": "Program", "issue_type": "epic", "status": "open", "priority": 0}
epic20 = {"id": "leverage-epic-20", "title": "Fat frontier", "issue_type": "epic", "status": "open", "priority": 1}
pair = [{"id": "leverage-a", "issue_type": "task"}, {"id": "leverage-b", "issue_type": "feature"}]
twenty = [{"id": f"leverage-{n}", "issue_type": "task"} for n in range(20)]
cmd = args[0] if args else ""
if cmd == "list":
    if "--parent" in args:
        ident = args[args.index("--parent") + 1]
        kids = pair if ident == "leverage-epic-1" else twenty if ident == "leverage-epic-20" else []
        print(json.dumps(kids))
    else:
        print(json.dumps([epic, epic20]))
elif cmd == "show":
    ident = positional[0] if positional else ""
    found = next((item for item in [epic, epic20, *pair, *twenty] if item["id"] == ident), None)
    print(json.dumps(found if found else {"error": "no issues found matching the provided IDs"}))
elif cmd == "children":
    ident = positional[0] if positional else ""
    kids = pair if ident == "leverage-epic-1" else twenty if ident == "leverage-epic-20" else []
    print(json.dumps(kids))
elif cmd == "ready":
    if "--parent" in args:
        ident = args[args.index("--parent") + 1]
        kids = pair if ident == "leverage-epic-1" else twenty if ident == "leverage-epic-20" else []
        print(json.dumps(kids))
    else:
        print(json.dumps([epic, *pair, *twenty]))
else:
    sys.exit(1)
'''

FAKE_FAT_READY_BR = r'''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
positional = [a for a in args[1:] if not a.startswith("-")] if args else []
epic = {"id": "leverage-epic-1", "title": "Program", "issue_type": "epic", "status": "open", "priority": 0}
pair = [{"id": "leverage-a", "issue_type": "task", "priority": 1}, {"id": "leverage-b", "issue_type": "feature", "priority": 1}]
noise = [{"id": f"other-{n}", "issue_type": "task", "priority": 0} for n in range(200)]
cmd = args[0] if args else ""
if cmd == "list":
    print(json.dumps(pair if "--parent" in args else [epic]))
elif cmd == "show":
    ident = positional[0] if positional else ""
    found = next((item for item in [epic, *pair] if item["id"] == ident), None)
    print(json.dumps(found if found else {"error": "no issues found matching the provided IDs"}))
elif cmd == "children":
    print(json.dumps(pair))
elif cmd == "ready":
    if "--parent" in args:
        print(json.dumps(pair))
    else:
        print(json.dumps(noise))
else:
    sys.exit(1)
'''

FAKE_EMPTY_BR = r'''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
epic = {"id": "leverage-epic-1", "title": "Program", "issue_type": "epic", "status": "open", "priority": 0}
cmd = args[0] if args else ""
if cmd == "list":
    print(json.dumps([epic]))
elif cmd == "show":
    print(json.dumps(epic))
elif cmd in ("children", "ready"):
    print("[]")
else:
    sys.exit(1)
'''

FAKE_CLOSED_BR = r'''#!/usr/bin/env python3
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parent
args = sys.argv[1:]
positional = [a for a in args[1:] if not a.startswith("-")] if args else []
epic = {"id": "leverage-epic-1", "title": "Program", "issue_type": "epic", "status": "open", "priority": 0}
kids = [
    {"id": "leverage-a", "issue_type": "task", "status": "closed"},
    {"id": "leverage-b", "issue_type": "feature", "status": "closed"},
]
done = root / "closed-br-done"
cmd = args[0] if args else ""
if cmd == "list":
    print(json.dumps(kids if "--parent" in args else [epic]))
elif cmd == "show":
    print(json.dumps({**epic, "status": "closed" if done.exists() else "open"}))
elif cmd == "children":
    print(json.dumps(kids))
elif cmd == "ready":
    print("[]")
elif cmd == "close":
    (root / "closed-br-close.json").write_text(json.dumps(args))
    done.write_text("1")
    print(json.dumps({**epic, "status": "closed"}))
else:
    sys.exit(1)
'''

FAKE_STUCK_BR = r'''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
epic = {"id": "leverage-epic-1", "title": "Program", "issue_type": "epic", "status": "open", "priority": 0}
kids = [
    {"id": "leverage-a", "issue_type": "task", "status": "in_progress"},
    {"id": "leverage-b", "issue_type": "task", "status": "open"},
]
cmd = args[0] if args else ""
if cmd == "list":
    print(json.dumps(kids if "--parent" in args else [epic]))
elif cmd == "show":
    print(json.dumps(epic))
elif cmd == "children":
    print(json.dumps(kids))
elif cmd == "ready":
    print("[]")
elif cmd == "close":
    sys.exit(1)
else:
    sys.exit(1)
'''

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
model = ""
if "--model" in args:
    model = args[args.index("--model") + 1]
if model == "fable":
    sys.stderr.write("extra usage exhausted\n")
    sys.exit(1)
print("pong")
'''

FAKE_GROK = r'''#!/usr/bin/env python3
print("pong")
'''

FAKE_AM_OK = r'''#!/usr/bin/env python3
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parent
(root / "am-log.txt").open("a").write(" ".join(sys.argv[1:]) + "\n")
if sys.argv[1:3] == ["file_reservations", "reserve"]:
    print(json.dumps({"granted": [{"id": 1, "path": "tmp/bead-swarm/launcher.lock", "exclusive": True}], "conflicts": []}))
else:
    print("{}")
'''

FAKE_AM_CONFLICT = r'''#!/usr/bin/env python3
import json, sys
if sys.argv[1:3] == ["file_reservations", "reserve"]:
    print(json.dumps({"granted": [], "conflicts": [{"path": "tmp/bead-swarm/launcher.lock", "holder": "AmberLake"}]}))
    sys.exit(0)
print("{}")
'''


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tmpdir()
        self.root = Path(self._tmp.name)
        self.cwd = self.root / "repo"
        self.cwd.mkdir()
        write_executable(self.root / "br", FAKE_BR)
        write_executable(self.root / "empty-br", FAKE_EMPTY_BR)
        write_executable(self.root / "closed-br", FAKE_CLOSED_BR)
        write_executable(self.root / "stuck-br", FAKE_STUCK_BR)
        write_executable(self.root / "fat-ready-br", FAKE_FAT_READY_BR)
        write_executable(self.root / "claude", FAKE_CLAUDE)
        write_executable(self.root / "grok", FAKE_GROK)
        write_executable(self.root / "am", FAKE_AM_OK)
        write_executable(self.root / "am-conflict", FAKE_AM_CONFLICT)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def env(self, br: str = "br", am: str = "am") -> dict[str, str]:
        env = isolated_env(self.root)
        env["BEAD_SWARM_BR_BIN"] = str(self.root / br)
        env["BR_BIN"] = str(self.root / br)
        env["BEAD_SWARM_AM_BIN"] = str(self.root / am)
        env["AM_BIN"] = str(self.root / am)
        env["BEAD_SWARM_CLAUDE_BIN"] = str(self.root / "claude")
        env["BEAD_SWARM_GROK_BIN"] = str(self.root / "grok")
        env["BEAD_SWARM_PROBE_TIMEOUT"] = "5"
        for key in (
            "PLANNING_MODELS",
            "BUILDING_MODELS",
            "BEAD_SWARM_PLANNING_MODELS",
            "BEAD_SWARM_BUILDING_MODELS",
            "BEAD_SWARM_SEAT_CACHE_TTL",
        ):
            env.pop(key, None)
        return env

    def swarm(self, args: list[str], env: dict[str, str]) -> CompletedProcess[str]:
        return run_cmd([str(SWARM), *args], cwd=self.cwd, env=env, timeout=20)

    def test_skips_fable_on_quota_and_lands_on_opus5(self) -> None:
        env = self.env()
        env["BEAD_SWARM_GROK_BIN"] = str(self.root / "no-such-grok")
        result = self.swarm(["--probe-only", "--reprobe"], env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clis:", result.stdout)
        self.assertIn("planning:", result.stdout)
        self.assertIn("building:", result.stdout)
        self.assertIn("orchestrator: opus5", result.stdout)
        self.assertIn("fable quota_dead", result.stdout)

    def test_planning_models_skips_to_named_rung(self) -> None:
        env = self.env()
        env["PLANNING_MODELS"] = '["claude/opus-5/xhigh"]'
        env["BEAD_SWARM_GROK_BIN"] = str(self.root / "no-such-grok")
        result = self.swarm(["--probe-only", "--reprobe"], env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("orchestrator: opus5", result.stdout)
        self.assertNotIn("fable quota_dead", result.stdout)
        self.assertIn("claude/opus-5/xhigh", result.stdout)

    def test_building_models_land_in_wave_prompt(self) -> None:
        env = self.env()
        env["BUILDING_MODELS"] = '["grok", "claude/opus/xhigh"]'
        result = self.swarm(
            [
                "--lab",
                "--seat",
                "grok",
                "--epic",
                "leverage-epic-1",
                "--once",
                "--wave-size",
                "1",
                "--max-waves",
                "1",
                "--stagger-seconds",
                "0",
                "--no-am",
            ],
            env,
        )
        prompts = list((self.cwd / "tmp" / "bead-swarm").glob("*/wave-1.prompt.md")) if (self.cwd / "tmp" / "bead-swarm").exists() else []
        self.assertTrue(prompts, result.stdout + result.stderr)
        text = prompts[0].read_text()
        self.assertIn("BUILDING_MODELS", text)
        self.assertIn("grok", text)

    def test_zero_cache_ttl_does_not_reuse_stale_choice(self) -> None:
        from datetime import datetime, timedelta, timezone

        (self.root / "seat-cache.json").write_text(
            json.dumps(
                {
                    "chosen": "grok",
                    "reason": "cached-test",
                    "skipped": [],
                    "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=1800)).isoformat(),
                }
            )
        )
        env = self.env()
        env["BEAD_SWARM_SEAT_CACHE_TTL"] = "0"
        env["BEAD_SWARM_GROK_BIN"] = str(self.root / "no-such-grok")
        result = self.swarm(["--probe-only"], env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("orchestrator: opus5", result.stdout)
        self.assertNotIn(" cached", result.stdout)

    def test_reuses_seat_cache_younger_than_one_hour(self) -> None:
        from datetime import datetime, timedelta, timezone

        (self.root / "seat-cache.json").write_text(
            json.dumps(
                {
                    "chosen": "grok",
                    "reason": "cached-test",
                    "skipped": [{"id": "fable", "reason": "quota_dead"}],
                    "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=1800)).isoformat(),
                }
            )
        )
        result = self.swarm(["--probe-only"], self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("orchestrator: grok", result.stdout)
        self.assertIn("cached", result.stdout)

    def test_plans_two_waves_of_ten(self) -> None:
        result = self.swarm(
            ["--dry-run", "--seat", "grok", "--epic", "leverage-epic-20", "--wave-size", "10", "--max-waves", "4"],
            self.env(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ready (non-epic, this epic): 20", result.stdout)
        self.assertIn("plan: 2 wave(s) × 10 beads", result.stdout)

    def test_frontier_dry_when_epic_has_no_ready_children(self) -> None:
        result = self.swarm(
            ["--dry-run", "--seat", "grok", "--epic", "leverage-epic-1"],
            self.env(br="empty-br"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("frontier dry", result.stdout)

    def test_refuses_to_nest(self) -> None:
        env = self.env()
        env["BEAD_SWARM_WAVE"] = "1"
        result = self.swarm(["--probe-only"], env)
        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing to nest", result.stderr)

    def test_scopes_ready_work_to_epic(self) -> None:
        result = self.swarm(
            ["--dry-run", "--seat", "composer", "--epic", "leverage-epic-1"],
            self.env(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ready (non-epic, this epic): 2", result.stdout)

    def test_ready_parent_not_swamped_by_global_p0_window(self) -> None:
        result = self.swarm(
            ["--dry-run", "--seat", "grok", "--epic", "leverage-epic-1"],
            self.env(br="fat-ready-br"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ready (non-epic, this epic): 2", result.stdout)

    def test_unknown_epic_exits_1(self) -> None:
        result = self.swarm(["--dry-run", "--seat", "grok", "--epic", "no-such-epic"], self.env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("epic not found: no-such-epic", result.stderr)

    def test_closes_epic_when_children_closed(self) -> None:
        env = self.env(br="closed-br")
        result = self.swarm(["--seat", "grok", "--epic", "leverage-epic-1", "--no-am"], env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("closed epic leverage-epic-1", result.stdout)
        logged = json.loads((self.root / "closed-br-close.json").read_text())
        self.assertIn("leverage-epic-1", logged)
        self.assertTrue(any("all 2 children closed" in str(item) for item in logged))

    def test_does_not_close_on_dry_run(self) -> None:
        result = self.swarm(
            ["--dry-run", "--seat", "grok", "--epic", "leverage-epic-1"],
            self.env(br="closed-br"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("frontier dry", result.stdout)
        self.assertNotIn("closed epic", result.stdout)
        self.assertFalse((self.root / "closed-br-close.json").exists())

    def test_prints_epics_when_none_passed(self) -> None:
        result = self.swarm(["--dry-run", "--seat", "grok"], self.env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("Open epics:", result.stdout)
        self.assertIn("pass --epic", result.stderr)

    def test_stuck_open_beads_exit_1_without_closing_epic(self) -> None:
        result = self.swarm(
            ["--seat", "grok", "--epic", "leverage-epic-1", "--no-am"],
            self.env(br="stuck-br"),
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("open beads", result.stdout)
        self.assertNotIn("closed epic", result.stdout)

    def test_launcher_lock_detects_json_conflicts_at_exit_zero(self) -> None:
        result = self.swarm(
            ["--lab", "--seat", "grok", "--epic", "leverage-epic-1"],
            self.env(am="am-conflict"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another bead-swarm launcher holds", result.stderr)


if __name__ == "__main__":
    unittest.main()
