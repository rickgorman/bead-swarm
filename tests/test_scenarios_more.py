from __future__ import annotations

import os
import shutil
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from beadswarm import amutil, bd, heartbeat, scavenge
from beadswarm import events as eventlog
from support import (
    BIN,
    ROOT,
    assert_expect_files,
    bd_path,
    isolated_env,
    make_lab,
    run_cmd,
    run_swarm,
    run_worker,
    show_status,
    start_swarm,
    start_worker,
    tmpdir,
    wait_until,
    warmup_am,
    write_executable,
)


def _has_tools() -> bool:
    return shutil.which("bd") is not None and shutil.which("am") is not None and shutil.which("git") is not None


@unittest.skipUnless(_has_tools(), "bd, am, and git required")
class ExtraScenarioTests(unittest.TestCase):
    def _prep(self, name: str):
        self._tmp = tmpdir()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        lab, spec, meta = make_lab(root, name)
        env = isolated_env(root)
        env["BEAD_SWARM_HOME"] = str(ROOT)
        warmup_am(lab, env)
        return lab, spec, meta, env, root

    def test_12_two_epics_does_not_touch_the_other_program(self) -> None:
        lab, spec, meta, env, _ = self._prep("12-two-epics-leak")
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(show_status(lab, meta["epic"], env), "closed")
        self.assertEqual(show_status(lab, meta["keymap"]["other"], env), "open")
        self.assertEqual(show_status(lab, meta["keymap"]["b1"], env), "open")
        self.assertFalse((lab / "files" / "b1.txt").exists())
        assert_expect_files(lab, spec["expect"])

    def test_13_partial_wrap_closes_only_the_done_slice(self) -> None:
        lab, spec, meta, env, _ = self._prep("13-partial-wrap")
        run_cmd(
            [bd_path(), "update", meta["keymap"]["b1"], "--claim", "--actor", "OtherFox", "--json"],
            cwd=lab,
            env=env,
        )
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(show_status(lab, meta["keymap"]["alpha"], env), "closed")
        self.assertEqual(show_status(lab, meta["keymap"]["a1"], env), "closed")
        self.assertEqual(show_status(lab, meta["epic"], env), "open")
        self.assertEqual(show_status(lab, meta["keymap"]["beta"], env), "open")
        self.assertEqual(show_status(lab, meta["keymap"]["b1"], env), "in_progress")

    def test_14_diamond(self) -> None:
        lab, spec, meta, env, _ = self._prep("14-diamond")
        self.assertEqual(meta["ready_width"], 1)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_15_partial_overlap(self) -> None:
        lab, spec, meta, env, _ = self._prep("15-partial-overlap")
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_16_shared_reader_blocks_exclusive_writer(self) -> None:
        lab, spec, meta, env, _ = self._prep("16-shared-vs-exclusive")
        reader = start_worker(lab, env, [meta["keymap"]["reader"]], wave=1)
        self.addCleanup(lambda: reader.poll() is None and reader.terminate())
        self.assertTrue(
            wait_until(lambda: "files/seed.txt" in run_cmd([env["AM_BIN"], "file_reservations", "list", str(lab)], cwd=lab, env=env).stdout),
            "reader never reserved seed.txt",
        )
        writer = run_worker(lab, env, [meta["keymap"]["writer"]], wave=2, timeout=30)
        self.assertEqual(writer.returncode, 0, writer.stdout + writer.stderr)
        reader.wait(timeout=15)
        self.assertEqual(reader.returncode, 0)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_17_no_am_rmw_loses_an_update(self) -> None:
        lab, spec, meta, env, _ = self._prep("17-no-am-rmw")
        a = start_worker(lab, env, [meta["keymap"]["a"]], wave=1)
        b = start_worker(lab, env, [meta["keymap"]["b"]], wave=2)
        self.addCleanup(lambda: a.poll() is None and a.terminate())
        self.addCleanup(lambda: b.poll() is None and b.terminate())
        self.assertTrue(
            wait_until(
                lambda: heartbeat.path_for(lab, meta["keymap"]["a"]).is_file()
                and heartbeat.path_for(lab, meta["keymap"]["b"]).is_file(),
                timeout=20,
            ),
            "both rmw workers should claim (heartbeat) before the gate opens",
        )
        (lab / "tmp" / "bead-swarm" / "rmw.gate").write_text("go\n")
        self.assertEqual(a.wait(timeout=15), 0)
        self.assertEqual(b.wait(timeout=15), 0)
        assert_expect_files(lab, spec["expect"])

    def test_18_second_launcher_bounces(self) -> None:
        lab, spec, meta, env, _ = self._prep("18-second-launcher")
        first = start_swarm(lab, env, meta)
        self.addCleanup(lambda: first.poll() is None and first.terminate())
        log = lab / "tmp" / "bead-swarm" / "launcher-foreground.log"
        self.assertTrue(
            wait_until(lambda: log.is_file() and "wave 1: spawned" in log.read_text(), timeout=20),
            "first launcher never spawned a wave: " + (log.read_text() if log.is_file() else "<no log>"),
        )
        listed = run_cmd([env["AM_BIN"], "file_reservations", "list", str(lab)], cwd=lab, env=env)
        self.assertTrue(
            wait_until(
                lambda: "launcher.lock" in run_cmd([env["AM_BIN"], "file_reservations", "list", str(lab)], cwd=lab, env=env).stdout,
                timeout=10,
            ),
            "first launcher never reserved launcher.lock: " + listed.stdout + listed.stderr,
        )
        second = run_swarm(lab, env, meta, timeout=30)
        blob = second.stdout + second.stderr
        self.assertNotEqual(second.returncode, 0, blob)
        self.assertIn("another bead-swarm launcher holds", blob)
        first.terminate()
        first.wait(timeout=10)

    def test_19_once_leaves_layer_two_then_full_run_finishes(self) -> None:
        lab, spec, meta, env, _ = self._prep("19-once-vs-recycle")
        first = run_swarm(lab, env, meta)
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self.assertIn("ready beads remain", first.stdout)
        self.assertEqual(show_status(lab, meta["epic"], env), "open")
        second = run_swarm(lab, env, meta, apply_run=False)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(show_status(lab, meta["epic"], env), "closed")
        assert_expect_files(lab, spec["expect"])

    def test_20_off_epic_bv_is_ignored(self) -> None:
        lab, spec, meta, env, root = self._prep("20-off-epic-bv")
        fake_bin = root / "fakebin"
        write_executable(
            fake_bin / "bv-robot-next",
            "#!/usr/bin/env python3\n"
            "import json\n"
            f"print(json.dumps({{'id': '{meta['keymap']['shiny1']}', 'actionable': True}}))\n",
        )
        env = dict(env)
        env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
        env["BEAD_SWARM_SKIP_BV"] = "0"
        result = run_worker(lab, env, [meta["keymap"]["a1"]], wave=1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreaterEqual(eventlog.count(lab, "off-epic-bv"), 1)
        self.assertEqual(show_status(lab, meta["keymap"]["a1"], env), "closed")
        self.assertEqual(show_status(lab, meta["keymap"]["shiny1"], env), "open")
        self.assertFalse((lab / "files" / "shiny.txt").exists())

    def test_21_foreign_actor_is_not_stolen(self) -> None:
        lab, spec, meta, env, _ = self._prep("21-foreign-actor")
        run_cmd(
            [bd_path(), "update", meta["keymap"]["only"], "--claim", "--actor", "OtherFox", "--json"],
            cwd=lab,
            env=env,
        )
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(show_status(lab, meta["keymap"]["only"], env), "in_progress")
        self.assertEqual(show_status(lab, meta["epic"], env), "open")
        self.assertFalse((lab / "files" / "only.txt").exists())

    def test_31_relates_does_not_block(self) -> None:
        lab, spec, meta, env, _ = self._prep("31-relates")
        self.assertEqual(meta["ready_width"], 2)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_32_p0_and_p4_both_finish(self) -> None:
        lab, spec, meta, env, _ = self._prep("32-p0-vs-p4")
        ready = bd.ready(lab, env=env)
        self.assertEqual([item.get("priority") for item in ready], [0, 4])
        self.assertEqual(ready[0]["id"], meta["keymap"]["high"])
        once = run_swarm(lab, env, meta)
        self.assertEqual(once.returncode, 1, once.stdout + once.stderr)
        self.assertTrue((lab / "files" / "high.txt").is_file(), "wave-size 1 --once must take P0")
        self.assertFalse((lab / "files" / "low.txt").exists(), "P4 must wait until the next wave")
        full = run_swarm(lab, env, meta, apply_run=False)
        self.assertEqual(full.returncode, 0, full.stdout + full.stderr)
        self.assertTrue((lab / "files" / "high.txt").is_file())
        self.assertTrue((lab / "files" / "low.txt").is_file())

    def test_33_idempotent_second_run(self) -> None:
        lab, spec, meta, env, _ = self._prep("33-idempotent-rerun")
        first = run_swarm(lab, env, meta)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = run_swarm(lab, env, meta)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(show_status(lab, meta["epic"], env), "closed")
        assert_expect_files(lab, spec["expect"])

    def test_35_future_gate_sha_is_not_claimable_when_bd_calls_it_ready(self) -> None:
        lab, spec, meta, env, _ = self._prep("35-future-gate-sha")
        proof_id = meta["keymap"]["proof"]
        gate_id = meta["keymap"]["gate"]
        candidate = lab / "tmp" / "bead-swarm" / "candidates" / "g00.sha"

        self.assertEqual(meta["ready_width"], 1)
        self.assertEqual([item["id"] for item in bd.ready(lab, env=env)], [proof_id])
        self.assertFalse(candidate.exists(), "the absorbed G00 candidate must not exist in this setup")
        self.assertIn(
            "semantically not ready even when `bd ready` reports it",
            bd.show(lab, proof_id, env=env)["description"],
        )

        result = run_swarm(lab, env, meta)

        self.assertEqual(result.returncode, spec["expect"]["exit_code"], result.stdout + result.stderr)
        self.assertEqual(show_status(lab, proof_id, env), "open")
        self.assertEqual(show_status(lab, gate_id, env), "open")
        self.assertEqual(show_status(lab, meta["epic"], env), "open")
        self.assertFalse((lab / "files" / "proof.txt").exists())
        self.assertFalse((lab / "files" / "gate.txt").exists())


@unittest.skipUnless(_has_tools(), "bd, am, and git required")
class CrashHangTests(unittest.TestCase):
    def _prep(self, name: str):
        self._tmp = tmpdir()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        lab, spec, meta = make_lab(root, name)
        env = isolated_env(root)
        env["BEAD_SWARM_HOME"] = str(ROOT)
        warmup_am(lab, env)
        return lab, spec, meta, env

    def test_22_finish_without_close_scavenger_closes(self) -> None:
        lab, spec, meta, env = self._prep("22-finish-without-close")
        worker = run_worker(lab, env, [meta["keymap"]["only"]], wave=1, fault="skip-close")
        self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
        self.assertEqual(show_status(lab, meta["keymap"]["only"], env), "in_progress")
        reports = scavenge.scavenge(lab, env=env)
        self.assertEqual(reports[0]["action"], "close")
        wrap = run_swarm(lab, env, meta)
        self.assertEqual(wrap.returncode, 0, wrap.stdout + wrap.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_23_crash_before_write_requeues(self) -> None:
        lab, spec, meta, env = self._prep("23-crash-before-write")
        crashed = run_worker(lab, env, [meta["keymap"]["only"]], wave=1, fault="die-after-reserve")
        self.assertNotEqual(crashed.returncode, 0)
        self.assertFalse((lab / "files" / "only.txt").exists())
        self.assertEqual(show_status(lab, meta["keymap"]["only"], env), "in_progress")
        time.sleep(0.05)
        reports = scavenge.scavenge(lab, env=env)
        self.assertEqual(reports[0]["action"], "requeue")
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_24_crash_after_write_scavenger_closes(self) -> None:
        lab, spec, meta, env = self._prep("24-crash-after-write")
        crashed = run_worker(lab, env, [meta["keymap"]["only"]], wave=1, fault="die-after-write")
        self.assertNotEqual(crashed.returncode, 0)
        self.assertTrue((lab / "files" / "only.txt").is_file())
        time.sleep(0.05)
        reports = scavenge.scavenge(lab, env=env)
        self.assertEqual(reports[0]["action"], "close")
        wrap = run_swarm(lab, env, meta)
        self.assertEqual(wrap.returncode, 0, wrap.stdout + wrap.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_25_hang_with_pid_is_not_stolen(self) -> None:
        lab, spec, meta, env = self._prep("25-hang-heartbeat")
        proc = start_worker(lab, env, [meta["keymap"]["only"]], wave=1, fault="hang-heartbeat", hang_seconds=20)
        self.addCleanup(lambda: proc.poll() is None and proc.terminate())
        self.assertTrue(wait_until(lambda: heartbeat.live(lab, meta["keymap"]["only"], max_age=2)))
        reports = scavenge.scavenge(lab, env=env)
        self.assertEqual(reports[0]["action"], "skip-live")
        self.assertEqual(show_status(lab, meta["keymap"]["only"], env), "in_progress")
        proc.terminate()
        proc.wait(timeout=5)

    def test_26_hang_without_heartbeat_still_not_stolen(self) -> None:
        lab, spec, meta, env = self._prep("26-hang-no-heartbeat")
        proc = start_worker(lab, env, [meta["keymap"]["only"]], wave=1, fault="hang-no-heartbeat", hang_seconds=20)
        self.addCleanup(lambda: proc.poll() is None and proc.terminate())
        self.assertTrue(wait_until(lambda: heartbeat.live(lab, meta["keymap"]["only"], max_age=2)))
        reports = scavenge.scavenge(lab, env=env)
        self.assertEqual(reports[0]["action"], "skip-live")
        proc.terminate()
        proc.wait(timeout=5)

    def test_27_hang_then_succeeds_after_scavenge_skip(self) -> None:
        lab, spec, meta, env = self._prep("27-hang-then-succeed")
        proc = start_worker(lab, env, [meta["keymap"]["only"]], wave=1, fault="hang-then-succeed", hang_seconds=0.6)
        self.addCleanup(lambda: proc.poll() is None and proc.terminate())
        self.assertTrue(wait_until(lambda: heartbeat.live(lab, meta["keymap"]["only"], max_age=2)))
        reports = scavenge.scavenge(lab, env=env)
        self.assertEqual(reports[0]["action"], "skip-live")
        self.assertEqual(proc.wait(timeout=15), 0)
        wrap = run_swarm(lab, env, meta)
        self.assertEqual(wrap.returncode, 0, wrap.stdout + wrap.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_28_nway_two_incomplete_then_scavenge(self) -> None:
        lab, spec, meta, env = self._prep("28-nway-two-incomplete")
        first = run_swarm(lab, env, meta)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("scavenge:", first.stdout)
        self.assertEqual(show_status(lab, meta["epic"], env), "closed")
        assert_expect_files(lab, spec["expect"])

    def test_29_close_without_release_blocks_until_holder_release(self) -> None:
        lab, spec, meta, env = self._prep("29-close-without-release")
        first = run_worker(lab, env, [meta["keymap"]["first"]], wave=1, fault="skip-release")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(show_status(lab, meta["keymap"]["first"], env), "closed")
        blocked = amutil.reserve(
            lab, "JadeFox", "files/shared.txt", exclusive=True, ttl=60, reason="probe", env=env, am_bin=env["AM_BIN"]
        )
        self.assertTrue(blocked["conflicted"], blocked["raw"])
        amutil.release(lab, "BlueLake", "files/shared.txt", env=env, am_bin=env["AM_BIN"])
        second = run_worker(lab, env, [meta["keymap"]["second"]], wave=2)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        wrap = run_swarm(lab, env, meta)
        self.assertEqual(wrap.returncode, 0, wrap.stdout + wrap.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_30_replay_after_unclaim_is_idempotent(self) -> None:
        lab, spec, meta, env = self._prep("30-replay-unclaim")
        first = run_worker(lab, env, [meta["keymap"]["only"]], wave=1, fault="skip-close")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        run_cmd(
            [bd_path(), "update", meta["keymap"]["only"], "--status", "open", "--assignee", "", "--json"],
            cwd=lab,
            env=env,
        )
        second = run_worker(lab, env, [meta["keymap"]["only"]], wave=2)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        wrap = run_swarm(lab, env, meta)
        self.assertEqual(wrap.returncode, 0, wrap.stdout + wrap.stderr)
        assert_expect_files(lab, spec["expect"])


class SeatCacheTests(unittest.TestCase):
    def test_spawn_failure_invalidates_seat_cache(self) -> None:
        from support import tmpdir as make_tmp

        tmp = make_tmp()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        write_executable(
            root / "grok",
            """#!/usr/bin/env python3
import sys
if "--single" in sys.argv:
    print("pong")
    raise SystemExit(0)
sys.stderr.write("429 rate limit extra usage\\n")
raise SystemExit(1)
""",
        )
        write_executable(
            root / "claude",
            """#!/usr/bin/env python3
import sys
sys.stderr.write("extra usage exhausted\\n")
raise SystemExit(1)
""",
        )
        env = isolated_env(root)
        env["BEAD_SWARM_GROK_BIN"] = str(root / "grok")
        env["BEAD_SWARM_CLAUDE_BIN"] = str(root / "claude")
        env["BEAD_SWARM_BR_BIN"] = str(Path(__file__).resolve().parent.parent / "tests")  # placeholder replaced below
        # Reuse launcher unit fake via a tiny br that has one ready bead.
        write_executable(
            root / "br",
            """#!/usr/bin/env python3
import json, sys
epic = {"id": "e1", "title": "E", "issue_type": "epic", "status": "open"}
kid = {"id": "t1", "issue_type": "task", "status": "open"}
cmd = sys.argv[1]
if cmd == "show":
    print(json.dumps(epic if sys.argv[2] == "e1" else kid))
elif cmd == "children":
    print(json.dumps([kid]))
elif cmd == "list":
    print(json.dumps([kid] if "--parent" in sys.argv else [epic]))
elif cmd == "ready":
    print(json.dumps([kid]))
else:
    raise SystemExit(1)
""",
        )
        env["BEAD_SWARM_BR_BIN"] = str(root / "br")
        env["BR_BIN"] = str(root / "br")
        cache = root / "seat-cache.json"
        env["BEAD_SWARM_SEAT_CACHE"] = str(cache)
        repo = root / "repo"
        repo.mkdir()
        probe = run_cmd([str(BIN / "bead-swarm"), "--probe-only", "--reprobe"], cwd=repo, env=env)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertTrue(cache.is_file())
        run = run_cmd(
            [str(BIN / "bead-swarm"), "--seat", "grok", "--epic", "e1", "--no-am", "--once", "--wave-size", "1", "--max-waves", "1", "--stagger-seconds", "0", "--cwd", str(repo)],
            cwd=repo,
            env=env,
            timeout=20,
        )
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(cache.is_file(), "wave exit 1 should drop the seat cache")


if __name__ == "__main__":
    unittest.main()
