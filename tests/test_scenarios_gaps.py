from __future__ import annotations

import fcntl
import os
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from beadswarm import amutil, bd, heartbeat, scavenge
from beadswarm import events as eventlog
from beadswarm.worker import AGENT_NAMES
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
    import shutil

    return shutil.which("bd") is not None and shutil.which("am") is not None


def _stop_proc(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    handle = getattr(proc, "_bead_swarm_log", None)
    if handle is not None:
        try:
            handle.close()
        except OSError:
            pass


@unittest.skipUnless(_has_tools(), "bd, am, and git required")
class GapTests(unittest.TestCase):
    def _prep(self, name: str, *, warm: bool = True):
        self._tmp = tmpdir()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        lab, spec, meta = make_lab(root, name)
        env = isolated_env(root)
        env["BEAD_SWARM_HOME"] = str(ROOT)
        if warm:
            warmup_am(lab, env)
        return lab, spec, meta, env, root

    def test_scavenge_inside_swarm_closes_skip_close(self) -> None:
        lab, spec, meta, env, _ = self._prep("22-finish-without-close")
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("scavenge:", result.stdout)
        self.assertEqual(show_status(lab, meta["epic"], env), "closed")
        assert_expect_files(lab, spec["expect"])

    def test_scavenge_inside_swarm_requeues_crash(self) -> None:
        lab, spec, meta, env, _ = self._prep("23-crash-before-write")
        crashed = run_worker(lab, env, [meta["keymap"]["only"]], wave=1, fault="die-after-reserve")
        self.assertNotEqual(crashed.returncode, 0)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("scavenge:", result.stdout)
        assert_expect_files(lab, spec["expect"])

    def test_hung_after_steals_stale_heartbeat(self) -> None:
        lab, spec, meta, env, _ = self._prep("26-hang-no-heartbeat")
        proc = start_worker(
            lab, env, [meta["keymap"]["only"]], wave=1, fault="hang-no-heartbeat", hang_seconds=20
        )
        self.addCleanup(lambda: proc.poll() is None and proc.terminate())
        self.assertTrue(wait_until(lambda: heartbeat.live(lab, meta["keymap"]["only"], max_age=2)))
        time.sleep(0.6)
        reports = scavenge.scavenge(lab, hung_after=0.3, env=env)
        self.assertEqual(reports[0]["kind"], scavenge.HUNG)
        self.assertEqual(reports[0]["action"], "requeue")
        proc.terminate()
        proc.wait(timeout=5)
        result = run_swarm(lab, env, meta)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(show_status(lab, meta["keymap"]["only"], env), "closed")

    def test_fresh_hang_not_stolen_even_with_hung_after(self) -> None:
        lab, spec, meta, env, _ = self._prep("25-hang-heartbeat")
        proc = start_worker(
            lab, env, [meta["keymap"]["only"]], wave=1, fault="hang-heartbeat", hang_seconds=20
        )
        self.addCleanup(lambda: proc.poll() is None and proc.terminate())
        self.assertTrue(wait_until(lambda: heartbeat.fresh(lab, meta["keymap"]["only"], max_age=1)))
        reports = scavenge.scavenge(lab, hung_after=0.3, env=env)
        self.assertEqual(reports[0]["action"], "skip-live")
        proc.terminate()
        proc.wait(timeout=5)

    def test_launcher_sigterm_releases_flock_second_swarm_recovers(self) -> None:
        lab, spec, meta, env, _ = self._prep("33-idempotent-rerun")
        hang_env = dict(env)
        hang_env["BEAD_SWARM_LAB_FAULT"] = "hang-heartbeat"
        hang_env["BEAD_SWARM_LAB_HANG_SECONDS"] = "25"
        first = start_swarm(lab, hang_env, meta, extra_args=["--no-scavenge"])
        log = lab / "tmp" / "bead-swarm" / "launcher-foreground.log"
        self.assertTrue(wait_until(lambda: log.is_file() and "wave 1: spawned" in log.read_text(), timeout=20))
        first.terminate()
        first.wait(timeout=10)
        second = run_swarm(lab, env, meta, timeout=60)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(show_status(lab, meta["epic"], env), "closed")

    def test_coder_alive_wave_dead_is_live(self) -> None:
        lab, spec, meta, env, _ = self._prep("22-finish-without-close")
        coder = start_worker(
            lab, env, [meta["keymap"]["only"]], wave=1, fault="hang-heartbeat", hang_seconds=20
        )
        self.addCleanup(lambda: coder.poll() is None and coder.terminate())
        self.assertTrue(wait_until(lambda: heartbeat.path_for(lab, meta["keymap"]["only"]).is_file()))
        data = heartbeat.read(lab, meta["keymap"]["only"])
        heartbeat.write(
            lab,
            meta["keymap"]["only"],
            "BlueLake",
            pid=int(data["pid"]),
            wave_pid=999999,
        )
        report = scavenge.classify(lab, meta["keymap"]["only"], env=env)
        self.assertTrue(heartbeat.pid_alive(int(data["pid"])))
        self.assertFalse(heartbeat.pid_alive(999999))
        self.assertEqual(report["kind"], scavenge.LIVE)
        coder.terminate()
        coder.wait(timeout=5)

    def test_append_replay_duplicates_line(self) -> None:
        lab, spec, meta, env, _ = self._prep("01-overlap-exclusive")
        bead = meta["keymap"]["a"]
        first = run_worker(lab, env, [bead], wave=1, fault="skip-close")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        run_cmd(
            [bd_path(), "update", bead, "--status", "open", "--assignee", "", "--json"],
            cwd=lab,
            env=env,
        )
        # Switch the remaining work to append by running the same bead again.
        second = run_worker(lab, env, [bead], wave=2)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        text = (lab / "files" / "shared.txt").read_text()
        self.assertGreaterEqual(len([ln for ln in text.splitlines() if ln.strip()]), 2)

    def test_reopen_child_after_wrap(self) -> None:
        lab, spec, meta, env, _ = self._prep("33-idempotent-rerun")
        first = run_swarm(lab, env, meta)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        run_cmd([bd_path(), "reopen", meta["keymap"]["only"], "--json"], cwd=lab, env=env)
        self.assertEqual(show_status(lab, meta["keymap"]["only"], env), "open")
        # reopen keeps the assignee, so ready --unassigned is empty until we clear it.
        run_cmd(
            [bd_path(), "update", meta["keymap"]["only"], "--assignee", "", "--json"],
            cwd=lab,
            env=env,
        )
        second = run_swarm(lab, env, meta)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(show_status(lab, meta["keymap"]["only"], env), "closed")

    def test_eacces_after_claim_leaves_in_progress(self) -> None:
        lab, spec, meta, env, _ = self._prep("33-idempotent-rerun")
        result = run_worker(lab, env, [meta["keymap"]["only"]], wave=1, fault="eacces-write")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(show_status(lab, meta["keymap"]["only"], env), "in_progress")
        self.assertGreaterEqual(eventlog.count(lab, "eacces"), 1)

    def test_close_twice_is_ok(self) -> None:
        lab, spec, meta, env, _ = self._prep("33-idempotent-rerun")
        result = run_worker(lab, env, [meta["keymap"]["only"]], wave=1, fault="close-twice")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreaterEqual(eventlog.count(lab, "close-twice"), 1)
        self.assertEqual(show_status(lab, meta["keymap"]["only"], env), "closed")

    def test_am_register_race_without_warmup(self) -> None:
        lab, spec, meta, env, _ = self._prep("07-claim-race", warm=False)
        a = start_worker(lab, env, [meta["keymap"]["only"]], wave=1)
        b = start_worker(lab, env, [meta["keymap"]["only"]], wave=2)
        self.addCleanup(lambda: a.poll() is None and a.terminate())
        self.addCleanup(lambda: b.poll() is None and b.terminate())
        self.assertEqual(a.wait(timeout=20), 0)
        self.assertEqual(b.wait(timeout=20), 0)
        self.assertGreaterEqual(eventlog.count(lab, "claim-miss") + eventlog.count(lab, "closed"), 1)

    def test_am_ttl_expiry_without_unclaim(self) -> None:
        lab, spec, meta, env, root = self._prep("08-abandoned-ttl")
        abandoned = run_worker(lab, env, [meta["keymap"]["stuck"]], wave=1, abandon=True)
        self.assertEqual(abandoned.returncode, 0, abandoned.stdout + abandoned.stderr)
        db = root / "am.db"
        self.assertTrue(db.is_file())
        con = sqlite3.connect(str(db))
        tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        expired = False
        for table in tables:
            cols = [row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()]
            ts_cols = [c for c in cols if "expir" in c.lower() or c.lower().endswith("_ts")]
            if not ts_cols:
                continue
            for col in ts_cols:
                con.execute(f"UPDATE {table} SET {col} = '2000-01-01T00:00:00Z'")
                expired = True
        con.commit()
        con.close()
        if not expired:
            self.skipTest(f"no expiry column in {tables}")
        granted = amutil.reserve(
            lab, "JadeFox", "files/stuck.txt", exclusive=True, ttl=60, reason="after-ttl", env=env, am_bin=env["AM_BIN"]
        )
        self.assertTrue(granted["ok"] or granted["conflicted"], granted)
        self.assertEqual(show_status(lab, meta["keymap"]["stuck"], env), "in_progress")

    def test_am_ttl_one_is_one_minute_not_one_second(self) -> None:
        lab, spec, meta, env, _ = self._prep("08-abandoned-ttl")
        stuck = meta["keymap"]["stuck"]
        claimed = run_cmd(
            [bd_path(), "update", stuck, "--claim", "--actor", "BlueLake", "--json"],
            cwd=lab,
            env=env,
        )
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        held = amutil.reserve(
            lab,
            "BlueLake",
            "files/stuck.txt",
            exclusive=True,
            ttl=1,
            reason="ttl-clock",
            env=env,
            am_bin=env["AM_BIN"],
        )
        self.assertTrue(held["ok"], held)
        blocked = amutil.reserve(
            lab,
            "JadeFox",
            "files/stuck.txt",
            exclusive=True,
            ttl=60,
            reason="ttl-probe",
            env=env,
            am_bin=env["AM_BIN"],
        )
        self.assertTrue(blocked["conflicted"], blocked)
        time.sleep(2)
        still = amutil.reserve(
            lab,
            "JadeFox",
            "files/stuck.txt",
            exclusive=True,
            ttl=60,
            reason="ttl-two-seconds",
            env=env,
            am_bin=env["AM_BIN"],
        )
        self.assertTrue(still["conflicted"] or still.get("busy"), still)
        started = time.time()

        def jade_got_it() -> bool:
            granted = amutil.reserve(
                lab,
                "JadeFox",
                "files/stuck.txt",
                exclusive=True,
                ttl=60,
                reason="ttl-wait",
                env=env,
                am_bin=env["AM_BIN"],
            )
            return bool(granted["ok"])

        self.assertTrue(wait_until(jade_got_it, timeout=75, interval=2), "AM --ttl 1 should expire around one minute")
        self.assertGreaterEqual(time.time() - started, 50)
        self.assertEqual(show_status(lab, stuck, env), "in_progress")

    def test_parallel_am_hits_mailbox_busy(self) -> None:
        lab, spec, meta, env, _ = self._prep("00-width6")
        def reserve_one(index: int) -> dict:
            return amutil.reserve(
                lab,
                AGENT_NAMES[index % len(AGENT_NAMES)],
                f"files/busy-{index}.txt",
                exclusive=True,
                ttl=60,
                reason=f"busy-{index}",
                env=env,
                am_bin=env["AM_BIN"],
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve_one, range(8)))
        self.assertTrue(any(item.get("busy") for item in results), results)
        live_busy = next(item for item in results if item.get("busy"))
        self.assertTrue(
            "temporarily busy" in live_busy["raw"].lower()
            or "activity lock" in live_busy["raw"].lower()
            or "database is locked" in live_busy["raw"].lower(),
            live_busy["raw"],
        )
        later = amutil.reserve(
            lab,
            "IvoryOwl",
            "files/busy-after.txt",
            exclusive=True,
            ttl=60,
            reason="after-busy",
            env=env,
            am_bin=env["AM_BIN"],
        )
        self.assertTrue(later["ok"] or later.get("busy") or later["conflicted"], later)
        if not later["ok"]:
            deadline = time.time() + 5
            granted = later
            while time.time() < deadline and not granted["ok"]:
                time.sleep(0.15)
                granted = amutil.reserve(
                    lab,
                    "IvoryOwl",
                    "files/busy-after.txt",
                    exclusive=True,
                    ttl=60,
                    reason="after-busy-retry",
                    env=env,
                    am_bin=env["AM_BIN"],
                )
            self.assertTrue(granted["ok"], granted)

    def test_six_way_am_exclusive_herd(self) -> None:
        lab, spec, meta, env, _ = self._prep("09-nway-log")
        current = 0
        max_seen = 0
        gate = threading.Barrier(6)
        lock = threading.Lock()

        def hold(agent: str) -> dict:
            nonlocal current, max_seen
            gate.wait()
            parsed: dict = {"ok": False}
            deadline = time.time() + 4
            while time.time() < deadline:
                parsed = amutil.reserve(
                    lab,
                    agent,
                    "files/log.txt",
                    exclusive=True,
                    ttl=60,
                    reason="six-way",
                    env=env,
                    am_bin=env["AM_BIN"],
                )
                if parsed["ok"]:
                    with lock:
                        current += 1
                        max_seen = max(max_seen, current)
                    time.sleep(0.2)
                    amutil.release(lab, agent, "files/log.txt", env=env, am_bin=env["AM_BIN"])
                    with lock:
                        current -= 1
                    return parsed
                if parsed.get("busy") and not parsed["conflicted"]:
                    time.sleep(0.05)
                    continue
                return parsed
            return parsed

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(hold, AGENT_NAMES))
        self.assertEqual(max_seen, 1, results)
        self.assertTrue(any(item["ok"] for item in results), results)

    def test_34_tiny_hang_skip_live_then_sigterm_releases_lock(self) -> None:
        lab, spec, meta, env, _ = self._prep("34-tiny-hang")
        first = start_swarm(lab, env, meta)
        third: subprocess.Popen[str] | None = None
        self.addCleanup(lambda: _stop_proc(third))
        self.addCleanup(lambda: _stop_proc(first))
        log = lab / "tmp" / "bead-swarm" / "launcher-foreground.log"
        self.assertTrue(
            wait_until(lambda: log.is_file() and "wave 1: spawned" in log.read_text(), timeout=20),
            "tiny-hang launcher never spawned: " + (log.read_text() if log.is_file() else "<no log>"),
        )
        # One hang bead is already claimed, so ready is empty: the second swarm
        # never reaches launcher.lock. It must skip-live, not steal.
        second = run_swarm(lab, env, meta, timeout=30)
        blob = second.stdout + second.stderr
        self.assertNotEqual(second.returncode, 0, blob)
        self.assertIn("skip-live", blob)
        self.assertIn("open beads", blob)
        self.assertNotIn("another bead-swarm launcher holds", blob)
        _stop_proc(first)
        hb = heartbeat.read(lab, meta["keymap"]["slow"])
        if hb and hb.get("pid"):
            wait_until(lambda: not heartbeat.pid_alive(int(hb["pid"])), timeout=5)
            if heartbeat.pid_alive(int(hb["pid"])):
                os.kill(int(hb["pid"]), 9)
                wait_until(lambda: not heartbeat.pid_alive(int(hb["pid"])), timeout=2)
        third = start_swarm(lab, env, meta)
        log3 = lab / "tmp" / "bead-swarm" / "launcher-foreground.log"
        self.assertTrue(
            wait_until(lambda: log3.is_file() and "wave 1: spawned" in log3.read_text(), timeout=20),
            "lock did not release after SIGTERM: " + (log3.read_text() if log3.is_file() else "<no log>"),
        )
        self.assertTrue(wait_until(lambda: heartbeat.path_for(lab, meta["keymap"]["slow"]).is_file(), timeout=8))
        alive = heartbeat.read(lab, meta["keymap"]["slow"])
        _stop_proc(third)
        if alive and alive.get("pid") and heartbeat.pid_alive(int(alive["pid"])):
            os.kill(int(alive["pid"]), 9)
            wait_until(lambda: not heartbeat.pid_alive(int(alive["pid"])), timeout=2)

    def test_children_hides_closed_list_parent_all_does_not(self) -> None:
        lab, spec, meta, env, _ = self._prep("33-idempotent-rerun")
        first = run_swarm(lab, env, meta)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        hidden = run_cmd([bd_path(), "children", meta["epic"], "--json"], cwd=lab, env=env)
        listed = run_cmd(
            [bd_path(), "list", "--parent", meta["epic"], "--all", "--json", "--limit", "0"],
            cwd=lab,
            env=env,
        )
        self.assertEqual(hidden.returncode, 0, hidden.stderr)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(bd.parse_issues(hidden.stdout), [])
        kids = bd.parse_issues(listed.stdout)
        self.assertEqual(len(kids), 1)
        self.assertEqual(kids[0].get("status"), "closed")
        self.assertEqual(kids[0].get("id"), meta["keymap"]["only"])

    def test_second_swarm_does_not_rewrite_closed_bead(self) -> None:
        lab, spec, meta, env, _ = self._prep("33-idempotent-rerun")
        first = run_swarm(lab, env, meta)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        path = lab / "files" / "only.txt"
        before = path.read_text()
        waves_before = list((lab / "tmp" / "bead-swarm").glob("*/wave-*.log")) if (lab / "tmp" / "bead-swarm").exists() else []
        second = run_swarm(lab, env, meta, extra_args=["--reprobe"])
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(path.read_text(), before)
        waves_after = list((lab / "tmp" / "bead-swarm").glob("*/wave-*.log"))
        self.assertEqual(len(waves_after), len(waves_before))
        self.assertIn("frontier dry", second.stdout)


class SixWayMutexTests(unittest.TestCase):
    def test_six_exclusive_flocks_never_overlap(self) -> None:
        tmp = tmpdir()
        self.addCleanup(tmp.cleanup)
        lock_path = Path(tmp.name) / "hot.log.lock"
        lock_path.write_text("")
        current = 0
        max_seen = 0
        gate = threading.Barrier(6)

        def hold() -> None:
            nonlocal current, max_seen
            gate.wait()
            fd = os.open(lock_path, os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX)
            current += 1
            max_seen = max(max_seen, current)
            time.sleep(0.02)
            current -= 1
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        threads = [threading.Thread(target=hold) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(max_seen, 1)


class WrapAndSeatGapTests(unittest.TestCase):
    def test_epic_close_failure_is_exit_1(self) -> None:
        from support import isolated_env, tmpdir as make_tmp, write_executable

        tmp = make_tmp()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        write_executable(
            root / "br",
            """#!/usr/bin/env python3
import json, sys
epic = {"id": "e1", "title": "E", "issue_type": "epic", "status": "open"}
kids = [
    {"id": "t1", "issue_type": "task", "status": "closed"},
    {"id": "t2", "issue_type": "task", "status": "closed"},
]
cmd = sys.argv[1]
if cmd == "show":
    print(json.dumps(epic))
elif cmd == "list":
    print(json.dumps(kids if "--parent" in sys.argv else [epic]))
elif cmd == "children":
    print(json.dumps(kids))
elif cmd == "ready":
    print("[]")
elif cmd == "close":
    sys.stderr.write("close failed\\n")
    raise SystemExit(1)
else:
    raise SystemExit(1)
""",
        )
        env = isolated_env(root)
        env["BEAD_SWARM_BR_BIN"] = str(root / "br")
        env["BR_BIN"] = str(root / "br")
        repo = root / "repo"
        repo.mkdir()
        result = run_cmd(
            [str(BIN / "bead-swarm"), "--seat", "grok", "--epic", "e1", "--no-am", "--cwd", str(repo)],
            cwd=repo,
            env=env,
        )
        self.assertEqual(result.returncode, 1)
        self.assertTrue(
            "epic remains open" in result.stdout or "epic(s) remain open" in result.stdout,
            result.stdout + result.stderr,
        )

    def test_quota_then_reprobe_does_not_need_old_cache(self) -> None:
        from support import isolated_env, tmpdir as make_tmp, write_executable

        tmp = make_tmp()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        flag = root / "fail-spawn"
        flag.write_text("1")
        write_executable(
            root / "grok",
            f"""#!/usr/bin/env python3
import sys
from pathlib import Path
flag = Path({str(flag)!r})
if "--single" in sys.argv:
    print("pong")
    raise SystemExit(0)
if flag.exists():
    sys.stderr.write("429 rate limit extra usage\\n")
    raise SystemExit(1)
print("WAVE_DONE")
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
        write_executable(
            root / "br",
            """#!/usr/bin/env python3
import json, sys
epic = {"id": "e1", "title": "E", "issue_type": "epic", "status": "open"}
kid = {"id": "t1", "issue_type": "task", "status": "open"}
cmd = sys.argv[1]
if cmd == "show":
    print(json.dumps(epic if "e1" in sys.argv else kid))
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
        env = isolated_env(root)
        env["BEAD_SWARM_GROK_BIN"] = str(root / "grok")
        env["BEAD_SWARM_CLAUDE_BIN"] = str(root / "claude")
        env["BEAD_SWARM_BR_BIN"] = str(root / "br")
        env["BR_BIN"] = str(root / "br")
        cache = root / "seat-cache.json"
        env["BEAD_SWARM_SEAT_CACHE"] = str(cache)
        repo = root / "repo"
        repo.mkdir()
        first = run_cmd(
            [str(BIN / "bead-swarm"), "--reprobe", "--epic", "e1", "--no-am", "--once", "--wave-size", "1", "--max-waves", "1", "--stagger-seconds", "0", "--cwd", str(repo)],
            cwd=repo,
            env=env,
        )
        self.assertNotEqual(first.returncode, 0)
        self.assertFalse(cache.is_file())
        flag.unlink()
        second = run_cmd(
            [str(BIN / "bead-swarm"), "--reprobe", "--epic", "e1", "--no-am", "--once", "--wave-size", "1", "--max-waves", "1", "--stagger-seconds", "0", "--cwd", str(repo)],
            cwd=repo,
            env=env,
        )
        logs = list((repo / "tmp" / "bead-swarm").glob("*/wave-1.log")) if (repo / "tmp" / "bead-swarm").exists() else []
        self.assertTrue(
            any("WAVE_DONE" in p.read_text() for p in logs),
            "reprobe wave log missing WAVE_DONE: " + (second.stdout + second.stderr),
        )

    def test_stale_open_child_then_second_swarm_wraps(self) -> None:
        from support import isolated_env, tmpdir as make_tmp, write_executable

        tmp = make_tmp()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        late = root / "kid-now-closed"
        write_executable(
            root / "br",
            f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
root = Path({str(root)!r})
late = root / "kid-now-closed"
done = root / "closed-br-done"
epic = {{"id": "e1", "title": "E", "issue_type": "epic", "status": "closed" if done.exists() else "open"}}
t1 = {{"id": "t1", "issue_type": "task", "status": "closed"}}
t2 = {{"id": "t2", "issue_type": "task", "status": "closed" if late.exists() else "open"}}
cmd = sys.argv[1]
if cmd == "show":
    ident = next((a for a in sys.argv[2:] if not a.startswith("-")), "")
    item = {{"e1": epic, "t1": t1, "t2": t2}}.get(ident, epic)
    print(json.dumps(item))
elif cmd == "list":
    print(json.dumps([t1, t2] if "--parent" in sys.argv else [epic]))
elif cmd == "children":
    print(json.dumps([] if late.exists() else [t2]))
elif cmd == "ready":
    print("[]")
elif cmd == "close":
    done.write_text("1")
    print(json.dumps({{**epic, "status": "closed"}}))
else:
    raise SystemExit(1)
""",
        )
        env = isolated_env(root)
        env["BEAD_SWARM_BR_BIN"] = str(root / "br")
        env["BR_BIN"] = str(root / "br")
        repo = root / "repo"
        repo.mkdir()
        first = run_cmd(
            [str(BIN / "bead-swarm"), "--seat", "grok", "--epic", "e1", "--no-am", "--cwd", str(repo)],
            cwd=repo,
            env=env,
        )
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self.assertIn("open beads", first.stdout)
        late.write_text("1")
        second = run_cmd(
            [str(BIN / "bead-swarm"), "--seat", "grok", "--epic", "e1", "--no-am", "--cwd", str(repo)],
            cwd=repo,
            env=env,
        )
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("closed epic e1", second.stdout)

    def test_reprobe_skips_spawn_when_tree_already_closed(self) -> None:
        from support import isolated_env, tmpdir as make_tmp, write_executable

        tmp = make_tmp()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        spawned = root / "spawned"
        write_executable(
            root / "grok",
            f"""#!/usr/bin/env python3
import sys
from pathlib import Path
if "--single" in sys.argv:
    print("pong")
    raise SystemExit(0)
Path({str(spawned)!r}).write_text("spawned")
print("WAVE_DONE")
""",
        )
        write_executable(
            root / "br",
            """#!/usr/bin/env python3
import json, sys
epic = {"id": "e1", "title": "E", "issue_type": "epic", "status": "closed"}
kid = {"id": "t1", "issue_type": "task", "status": "closed"}
cmd = sys.argv[1]
if cmd == "show":
    print(json.dumps(epic if "e1" in sys.argv else kid))
elif cmd == "children":
    print("[]")
elif cmd == "list":
    print(json.dumps([kid] if "--parent" in sys.argv else [epic]))
elif cmd == "ready":
    print("[]")
elif cmd == "close":
    print(json.dumps(epic))
else:
    raise SystemExit(1)
""",
        )
        env = isolated_env(root)
        env["BEAD_SWARM_GROK_BIN"] = str(root / "grok")
        env["BEAD_SWARM_BR_BIN"] = str(root / "br")
        env["BR_BIN"] = str(root / "br")
        repo = root / "repo"
        repo.mkdir()
        result = run_cmd(
            [
                str(BIN / "bead-swarm"),
                "--reprobe",
                "--epic",
                "e1",
                "--no-am",
                "--once",
                "--wave-size",
                "1",
                "--max-waves",
                "1",
                "--stagger-seconds",
                "0",
                "--cwd",
                str(repo),
            ],
            cwd=repo,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("frontier dry", result.stdout)
        self.assertFalse(spawned.exists(), "closed tree must not spawn a wave")


if __name__ == "__main__":
    unittest.main()
