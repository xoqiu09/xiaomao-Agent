"""Real lock contention: a paused `xiaomao scan` process versus other entries.

A `git` wrapper on PATH pauses the scan subprocess inside `git status` (a slow
worktree) until a release file appears. No exception is monkeypatched: the
contender meets whatever SQLite and the collection lock actually do.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from tests.helpers import init_repo, website_fixture_config
from xiaomao.config import save_config
from xiaomao.store import SCHEMA_VERSION, open_db, open_db_readonly

SRC = Path(__file__).resolve().parents[1] / "src"
WRAPPER = """#!/bin/sh
for a in "$@"; do
  if [ "$a" = "status" ] && [ -n "$XM_PAUSE_DIR" ] && [ ! -e "$XM_PAUSE_DIR/release" ]; then
    : > "$XM_PAUSE_DIR/ready"
    n=0
    while [ ! -e "$XM_PAUSE_DIR/release" ] && [ $n -lt 1200 ]; do sleep 0.05; n=$((n+1)); done
    break
  fi
done
exec "%s" "$@"
"""


class LockContentionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        bindir = self.root / "bin"
        bindir.mkdir()
        (bindir / "git").write_text(WRAPPER % shutil.which("git"))
        (bindir / "git").chmod(0o755)
        self.repo = init_repo(self.root / "repo")
        self.home = self.root / "home"
        self.home.mkdir()
        save_config(website_fixture_config(self.home, self.repo, extra_root=self.root))
        self.env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONDONTWRITEBYTECODE="1",
                        HOME=str(self.root / "fakehome"), PATH=f"{bindir}:{os.environ['PATH']}")
        self.env.pop("XM_PAUSE_DIR", None)
        self.cli("scan")

    def cli(self, *argv, env=None, timeout=120):
        return subprocess.run([sys.executable, "-m", "xiaomao", "--home", str(self.home), *argv],
                              env=env or self.env, capture_output=True, text=True, timeout=timeout)

    def paused_scan(self):
        pause = self.root / "pause"
        shutil.rmtree(pause, ignore_errors=True)
        pause.mkdir()
        holder = subprocess.Popen([sys.executable, "-m", "xiaomao", "--home", str(self.home), "scan"],
                                  env=dict(self.env, XM_PAUSE_DIR=str(pause)), text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 60
        while not (pause / "ready").exists():
            self.assertIsNone(holder.poll(), "scan exited before reaching git status")
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        return holder, pause / "release"

    def finish(self, holder, release):
        release.write_text("go")
        out, err = holder.communicate(timeout=120)
        self.assertEqual(holder.returncode, 0, err)

    def test_paused_scan_does_not_hold_the_sqlite_write_lock(self):
        holder, release = self.paused_scan()
        try:
            # A real independent writer gets the write lock immediately.
            with sqlite3.connect(self.home / "xiaomao.sqlite", timeout=0) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()
            started = time.monotonic()
            health = self.cli("health")
            status = self.cli("status", "--project", "website")
            elapsed = time.monotonic() - started
        finally:
            self.finish(holder, release)
        self.assertEqual(health.returncode, 0, health.stderr)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertNotIn("database is locked", health.stderr + status.stderr)
        self.assertLess(elapsed, 20)

    def test_second_scan_skips_fast_and_records_skip_once(self):
        holder, release = self.paused_scan()
        try:
            started = time.monotonic()
            second = self.cli("scan")
            elapsed = time.monotonic() - started
        finally:
            self.finish(holder, release)
        self.assertEqual(second.returncode, 3)
        self.assertIn('"lock_busy"', second.stdout)
        self.assertLess(elapsed, 10)
        with open_db_readonly(self.home / "xiaomao.sqlite") as conn:
            skips = conn.execute("SELECT COUNT(*) FROM scan_runs WHERE last_safe_error='lock_busy'").fetchone()[0]
            events = conn.execute("SELECT COUNT(*) FROM events WHERE kind='scan_skip'").fetchone()[0]
        self.assertEqual((skips, events), (1, 1))

    def test_daily_waits_bounded_then_publishes_from_stored_collection(self):
        holder, release = self.paused_scan()
        try:
            started = time.monotonic()
            daily = subprocess.run(
                [sys.executable, "-c",
                 "import sys; from pathlib import Path\n"
                 "from xiaomao.config import load_config\n"
                 "from xiaomao.daily_jobs import run_daily\n"
                 "cfg = load_config(Path(sys.argv[1])); cfg.home = sys.argv[1]\n"
                 "print(run_daily(cfg, date='2026-10-02', lock_wait_s=2))",
                 str(self.home)],
                env=self.env, capture_output=True, text=True, timeout=120)
            elapsed = time.monotonic() - started
        finally:
            self.finish(holder, release)
        self.assertEqual(daily.returncode, 0, daily.stderr)
        self.assertLess(elapsed, 20)
        evidence = self.home / "reports/daily-evidence/2026-10-02.txt"
        self.assertIn("另一次扫描仍在进行", evidence.read_text())
        # After the lock is released a normal daily collects again.
        again = self.cli("daily", "--date", "2026-10-02")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn("另一次扫描仍在进行", evidence.read_text())


class ReadOnlyEntryTests(unittest.TestCase):
    def test_current_schema_open_and_readonly_queries_write_nothing(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        db = root / "xiaomao.sqlite"
        with open_db(db):
            pass
        with closing(sqlite3.connect(db)) as watcher:
            version = watcher.execute("PRAGMA schema_version").fetchone()[0]
            changes = watcher.total_changes
            with open_db(db) as conn:
                self.assertEqual(conn.total_changes, 0)  # no DDL/meta rewrite
            with open_db_readonly(db) as conn:
                self.assertEqual(conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0],
                                 SCHEMA_VERSION)
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("INSERT INTO meta VALUES ('x','y')")
            self.assertEqual(watcher.execute("PRAGMA schema_version").fetchone()[0], version)
            self.assertEqual(watcher.total_changes, changes)


if __name__ == "__main__":
    unittest.main()
