from __future__ import annotations

import shutil
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from beadswarm import amutil, bd
from beadswarm import events as eventlog
from support import (
    assert_expect_files,
    bd_path,
    isolated_env,
    make_lab,
    run_cmd,
    run_swarm,
    run_worker,
    show_status,
    tmpdir,
    warmup_am,
)


def _has_tools() -> bool:
    return shutil.which("bd") is not None and shutil.which("am") is not None and shutil.which("git") is not None


@unittest.skipUnless(_has_tools(), "bd, am, and git required")
class ScenarioTests(unittest.TestCase):
    def _prep(self, name: str):
        self._tmp = tmpdir()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        lab, spec, meta = make_lab(root, name)
        env = isolated_env(root)
        warmup_am(lab, env)
        return lab, spec, meta, env

    def test_00_width6_ready_width_and_files(self) -> None:
        lab, spec, meta, env = self._prep("00-width6")
        self.assertEqual(meta["ready_width"], 6)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("frontier dry", result.stdout)
        self.assertEqual(show_status(lab, meta["epic"], env), "closed")
        assert_expect_files(lab, spec["expect"])

    def test_01_overlap_exclusive_keeps_both_appends(self) -> None:
        lab, spec, meta, env = self._prep("01-overlap-exclusive")
        self.assertEqual(meta["ready_width"], 2)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])
        retries = eventlog.count(lab, "am-conflict") + eventlog.count(lab, "am-busy")
        self.assertGreaterEqual(retries, spec["expect"]["am_retries_min"])
        self.assertEqual(show_status(lab, meta["epic"], env), "closed")

    def test_02_touch_label_serializes_hotspot(self) -> None:
        lab, spec, meta, env = self._prep("02-touch-label")
        for key in ("left", "right"):
            shown = bd.show(lab, meta["keymap"][key], env=env)
            self.assertIn("touch:files/hotspot.txt", shown.get("labels") or [])
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_03_blocks_same_file_is_ordered_without_am_conflicts(self) -> None:
        lab, spec, meta, env = self._prep("03-blocks-same-file")
        self.assertEqual(meta["ready_width"], 1)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])
        self.assertLessEqual(eventlog.count(lab, "am-conflict"), spec["expect"]["am_conflicts_max"])

    def test_04_fan_in_merge(self) -> None:
        lab, spec, meta, env = self._prep("04-fan-in-merge")
        self.assertEqual(meta["ready_width"], 3)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_05_fan_out_shared_seed(self) -> None:
        lab, spec, meta, env = self._prep("05-fan-out-shared")
        self.assertEqual(meta["ready_width"], 1)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])

    def test_06_two_disjoint_clusters(self) -> None:
        lab, spec, meta, env = self._prep("06-two-clusters")
        self.assertEqual(meta["ready_width"], 2)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])
        self.assertEqual(show_status(lab, meta["epic"], env), "closed")

    def test_07_claim_race(self) -> None:
        lab, spec, meta, env = self._prep("07-claim-race")
        bead_id = meta["keymap"]["only"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(run_worker, lab, env, [bead_id], wave=1)
            f2 = pool.submit(run_worker, lab, env, [bead_id], wave=2)
            r1, r2 = f1.result(), f2.result()
        self.assertTrue(r1.returncode == 0 or r2.returncode == 0, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        self.assertGreaterEqual(eventlog.count(lab, "claim-miss"), spec["expect"]["claim_misses_min"])
        self.assertEqual(eventlog.count(lab, "closed"), spec["expect"]["closed_beads"])
        assert_expect_files(lab, spec["expect"])
        self.assertEqual(show_status(lab, bead_id, env), "closed")

    def test_08_abandoned_ttl_does_not_unclaim(self) -> None:
        lab, spec, meta, env = self._prep("08-abandoned-ttl")
        bead_id = meta["keymap"]["stuck"]
        abandoned = run_worker(lab, env, [bead_id], wave=1, abandon=True)
        self.assertEqual(abandoned.returncode, 0, abandoned.stdout + abandoned.stderr)
        self.assertEqual(show_status(lab, bead_id, env), "in_progress")
        self.assertEqual(eventlog.count(lab, "held"), 1)

        listed = run_cmd(
            [env["AM_BIN"], "file_reservations", "list", str(lab)],
            cwd=lab,
            env=env,
        )
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("files/stuck.txt", listed.stdout)
        self.assertEqual(show_status(lab, bead_id, env), "in_progress")
        self.assertEqual(len(bd.ready(lab, env=env)), 0)

        blocked = amutil.reserve(
            lab,
            "JadeFox",
            "files/stuck.txt",
            exclusive=True,
            ttl=60,
            reason="reclaim-probe",
            env=env,
            am_bin=env["AM_BIN"],
        )
        self.assertTrue(blocked["conflicted"], blocked["raw"])

        # AM --ttl 1 is one minute despite the CLI help; unclaim alone is not enough.
        run_cmd(
            [bd_path(), "update", bead_id, "--status", "open", "--assignee", "", "--json"],
            cwd=lab,
            env=env,
        )
        amutil.release(lab, "BlueLake", "files/stuck.txt", env=env, am_bin=env["AM_BIN"])
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(show_status(lab, meta["epic"], env), "closed")
        assert_expect_files(lab, spec["expect"])

    def test_09_nway_log_keeps_every_line(self) -> None:
        lab, spec, meta, env = self._prep("09-nway-log")
        self.assertEqual(meta["ready_width"], 6)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])
        retries = eventlog.count(lab, "am-conflict") + eventlog.count(lab, "am-busy")
        self.assertGreaterEqual(retries, spec["expect"]["am_retries_min"])

    def test_10_nested_epics_close_inside_out(self) -> None:
        lab, spec, meta, env = self._prep("10-nested-epics")
        self.assertEqual(meta["ready_width"], 2)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assert_expect_files(lab, spec["expect"])
        self.assertEqual(show_status(lab, meta["epic"], env), "closed")
        for key in ("alpha", "beta"):
            self.assertEqual(show_status(lab, meta["keymap"][key], env), "closed")
        self.assertIn("closed epic", result.stdout)

    def test_11_cycle_rejected_and_stuck_blocker_does_not_wrap(self) -> None:
        lab, spec, meta, env = self._prep("11-cycle-trap")
        self.assertEqual(meta["ready_width"], 1)
        self.assertIn("_cycle_error", meta["keymap"])
        self.assertIn("cycle", meta["keymap"]["_cycle_error"].lower())

        claimed = run_cmd(
            [bd_path(), "update", meta["keymap"]["a"], "--claim", "--json"],
            cwd=lab,
            env=env,
        )
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        self.assertEqual(len(bd.ready(lab, env=env)), 0)

        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, spec["expect"]["exit_code"], result.stdout + result.stderr)
        self.assertIn(spec["expect"]["stdout_contains"], result.stdout)
        self.assertEqual(show_status(lab, meta["epic"], env), "open")
        self.assertEqual(show_status(lab, meta["keymap"]["a"], env), "in_progress")
        self.assertEqual(show_status(lab, meta["keymap"]["b"], env), "open")


if __name__ == "__main__":
    unittest.main()
