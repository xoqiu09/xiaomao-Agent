import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import git, init_repo, website_fixture_config
from xiaomao.config import load_config, save_config


class InventoryTests(unittest.TestCase):
    def test_migration_adds_daily_tables_to_legacy_database(self):
        import sqlite3
        from xiaomao.store import SCHEMA, open_db
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        db = root / "legacy.sqlite"
        with sqlite3.connect(db) as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO meta VALUES ('schema','2')")
            conn.execute("INSERT INTO meta VALUES ('legacy_marker','preserve')")
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name='daily_samples'").fetchone())
        with open_db(db) as conn:
            self.assertEqual(conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], "5")
            self.assertEqual(conn.execute("SELECT value FROM meta WHERE key='legacy_marker'").fetchone()[0],
                             "preserve")
            self.assertIsNotNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name='daily_samples'").fetchone())
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_missing_root_is_visible_as_inventory_gap(self):
        from xiaomao.inventory import effective_config
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = init_repo(root / "repo")
        cfg = website_fixture_config(root / "home", repo)
        cfg.personal_roots = [str(root / "missing")]
        self.assertTrue(effective_config(cfg)._inventory_errors)

    def test_replaced_discovered_tree_rejected_before_source_read(self):
        from xiaomao.inventory import effective_config
        from xiaomao.collect import scan_authorized
        from xiaomao.store import open_db
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = init_repo(root / "repo")
        linked = root / "linked"
        git(repo, "worktree", "add", "--detach", str(linked))
        cfg = website_fixture_config(root / "home", repo)
        cfg.projects[0].worktree_policy = "all"
        effective = effective_config(cfg)
        linked.rename(root / "saved")
        init_repo(linked)
        from xiaomao.collect import collect_snapshot
        calls = []
        def collect(path):
            calls.append(path)
            return collect_snapshot(path)
        with open_db(root / "home/xiaomao.sqlite") as conn, patch(
                "xiaomao.collect.collect_snapshot", side_effect=collect):
            scan_authorized(conn, effective)
        self.assertNotIn(linked.resolve(), [path.resolve() for path in calls])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = init_repo(self.root / "personal")
        self.cfg = website_fixture_config(self.root / "home", self.repo)

    def test_new_configuration_roundtrip(self):
        self.cfg.daily_time = "22:15"
        self.cfg.personal_roots = [str(self.root)]
        self.cfg.daily_notify = False
        self.cfg.projects[0].worktree_policy = "all"
        save_config(self.cfg)
        loaded = load_config(self.root / "home")
        self.assertEqual(getattr(loaded, "daily_time", None), "22:15")
        self.assertEqual(loaded.personal_roots, [str(self.root)])
        self.assertEqual(loaded.projects[0].worktree_policy, "all")
        self.assertFalse(loaded.daily_notify)

    def test_personal_clones_group_and_unknown_is_candidate_only(self):
        from xiaomao.inventory import effective_config, discover
        git(self.repo, "remote", "add", "origin", "git@github-personal:xoqiu09/sample.git")
        clone = self.root / "copy"
        git(self.root, "clone", str(self.repo), str(clone))
        git(clone, "remote", "set-url", "origin", "https://github.com/xoqiu09/sample.git")
        init_repo(self.root / "unknown")
        self.cfg.personal_roots = [str(self.root)]
        rows = discover(self.cfg)
        self.assertEqual(next(r for r in rows if r["path"].endswith("/unknown"))["status"], "candidate")
        effective = effective_config(self.cfg)
        self.assertEqual(len(effective.projects), 1)
        self.assertEqual({Path(w.path).name for w in effective.projects[0].worktrees}, {"personal", "copy"})

    def test_all_includes_detached_and_excludes_release_and_disabled(self):
        from xiaomao.inventory import effective_config
        from xiaomao.config import WorktreeSpec
        detached = self.root / "detached"
        disabled = self.root / "disabled"
        release = self.root / "Library/Application Support/Xiaomao/release/test"
        git(self.repo, "worktree", "add", "--detach", str(detached))
        git(self.repo, "worktree", "add", "--detach", str(disabled))
        git(self.repo, "worktree", "add", "--detach", str(release))
        self.cfg.projects[0].worktree_policy = "all"
        self.cfg.projects[0].worktrees.append(WorktreeSpec("disabled", str(disabled), scan=False))
        effective = effective_config(self.cfg)
        active = {Path(w.path).name for w in effective.projects[0].worktrees if w.scan}
        self.assertEqual(active, {"personal", "detached"})

    def test_excluded_paths_never_reach_git(self):
        from xiaomao.inventory import discover
        forbidden = self.root / "theAIapp-service-secret"
        forbidden.mkdir()
        (forbidden / ".git").mkdir()
        self.cfg.personal_roots = [str(forbidden)]
        with patch("xiaomao.inventory.run_git", side_effect=AssertionError("excluded Git read")):
            self.assertEqual(discover(self.cfg), [])
