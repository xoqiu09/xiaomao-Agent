from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import git, init_repo, website_fixture_config
from xiaomao.collect import scan_authorized
from xiaomao.reports import render_daily
from xiaomao.store import open_db


class DailyRegressionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = init_repo(self.root / "repo")
        self.cfg = website_fixture_config(self.root / "home", self.repo)
        self.conn = self.enterContext(open_db(self.root / "home/xiaomao.sqlite"))

    def scan(self, stamp):
        with patch("xiaomao.collect.utc_now", return_value=stamp):
            return scan_authorized(self.conn, self.cfg)["website"][0]

    def test_second_edit_of_same_dirty_file_is_observed(self):
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "README.md").write_text("first edit\n")
        first = self.scan("2026-10-02T03:00:00+00:00")
        (self.repo / "README.md").write_text("second edit\n")
        second = self.scan("2026-10-02T03:05:00+00:00")
        self.assertTrue(second["inserted"], "same path/status must not hide new content")
        self.assertNotEqual(first["observation_id"], second["observation_id"])

    def test_committed_and_clean_work_is_in_daily(self):
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "retry.py").write_text("def retry():\n    return 'recovered'\n")
        git(self.repo, "add", "retry.py")
        git(self.repo, "commit", "-m", "Fix retry recovery")
        self.scan("2026-10-02T03:00:00+00:00")
        body = render_daily(self.cfg, self.conn, date="2026-10-02")
        self.assertIn("Fix retry recovery", body)
        self.assertNotIn("授权观察范围内无新变化", body)

    def bundle(self, date="2026-10-02"):
        from datetime import datetime, timezone
        from xiaomao.daily import build_bundle
        return build_bundle(self.cfg, self.conn, date=date, now=datetime(2026, 10, 3, tzinfo=timezone.utc))

    def test_unchanged_old_dirty_is_ongoing_not_new_work(self):
        (self.repo / "README.md").write_text("unfinished yesterday\n")
        self.scan("2026-10-01T12:00:00+00:00")
        self.scan("2026-10-02T03:00:00+00:00")
        project = self.bundle()["projects"][0]
        self.assertFalse(project["changed"])
        self.assertEqual(len(project["ongoing"]), 1)
        self.assertEqual(project["activity"], [])

    def test_first_registration_is_baseline_not_full_history(self):
        self.scan("2026-10-02T03:00:00+00:00")
        project = self.bundle()["projects"][0]
        self.assertFalse(project["changed"])
        self.assertEqual(project["commits"], [])
        self.assertTrue(project["gaps"])

    def test_cutoff_boundary_and_late_work_go_to_next_day(self):
        from xiaomao.daily import report_window
        start, end = report_window(self.cfg, "2026-10-02")
        self.assertEqual(start.isoformat(), "2026-10-01T13:30:00+00:00")
        self.assertEqual(end.isoformat(), "2026-10-02T13:30:00+00:00")
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "README.md").write_text("after cutoff\n")
        self.scan("2026-10-02T13:30:00+00:00")
        self.assertFalse(self.bundle()["projects"][0]["changed"])
        self.assertTrue(self.bundle("2026-10-03")["projects"][0]["changed"])

    def test_observed_revert_has_evidence(self):
        self.scan("2026-10-01T13:00:00+00:00")
        original = (self.repo / "README.md").read_text()
        (self.repo / "README.md").write_text("temporary idea\n")
        self.scan("2026-10-02T03:00:00+00:00")
        (self.repo / "README.md").write_text(original)
        self.scan("2026-10-02T03:05:00+00:00")
        project = self.bundle()["projects"][0]
        self.assertEqual(project["ongoing"], [])
        self.assertIn("restored", [a["kind"] for a in project["activity"]])

    def test_two_worktrees_and_clone_deduplicate_commit(self):
        linked = self.root / "linked"
        git(self.repo, "remote", "add", "origin", "https://github.com/xoqiu09/fixture.git")
        git(self.repo, "worktree", "add", "--detach", str(linked))
        self.cfg.projects[0].worktree_policy = "all"
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "feature.py").write_text("feature = True\n")
        git(self.repo, "add", "feature.py")
        git(self.repo, "commit", "-m", "Ship fixture feature")
        self.scan("2026-10-02T03:00:00+00:00")
        project = self.bundle()["projects"][0]
        self.assertEqual(len(project["commits"]), 1)
        self.assertEqual(len(project["trees"]), 2)

    def test_error_is_partial_coverage_not_no_work_claim(self):
        self.scan("2026-10-01T13:00:00+00:00")
        self.repo.rename(self.root / "temporarily-missing")
        self.scan("2026-10-02T03:00:00+00:00")
        bundle = self.bundle()
        self.assertEqual(bundle["coverage"], "partial")
        self.assertTrue(bundle["projects"][0]["gaps"])

    def test_never_observed_failed_tree_remains_in_coverage_inventory(self):
        self.repo.rename(self.root / "missing")
        self.scan("2026-10-02T03:00:00+00:00")
        trees = self.bundle()["projects"][0]["trees"]
        self.assertEqual([t["tree_id"] for t in trees], ["website-main"])
        self.assertEqual(trees[0]["status"], "unobserved")

    def test_same_commit_later_seen_in_copy_does_not_repeat_next_day(self):
        from xiaomao.config import WorktreeSpec
        git(self.repo, "remote", "add", "origin", "https://github.com/xoqiu09/fixture.git")
        copy = self.root / "copy"
        git(self.root, "clone", str(self.repo), str(copy))
        git(copy, "remote", "set-url", "origin", "https://github.com/xoqiu09/fixture.git")
        self.cfg.projects[0].worktrees.append(WorktreeSpec("copy", str(copy)))
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "feature.py").write_text("feature = True\n")
        git(self.repo, "add", ".")
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-09-29T12:00:00+00:00"}):
            git(self.repo, "commit", "-m", "One feature")
        self.scan("2026-10-02T03:00:00+00:00")
        # Local fixture transfer only; production collection never fetches.
        git(copy, "fetch", str(self.repo), "HEAD")
        git(copy, "merge", "--ff-only", "FETCH_HEAD")
        self.scan("2026-10-03T03:00:00+00:00")
        self.assertEqual(len(self.bundle()["projects"][0]["commits"]), 1)
        self.assertEqual(self.bundle("2026-10-03")["projects"][0]["commits"], [])

    def test_dedup_uses_earliest_observation_regardless_of_tree_order(self):
        from tests.daily_scenario import generate
        bundle, _, _ = generate(self.root / "scenario")
        commit = bundle["projects"][0]["commits"][0]
        self.assertEqual(commit["first_seen"], "2026-10-02T03:05:00+00:00")

    def test_supplementary_tree_disappears_but_keeps_window_history(self):
        linked = self.root / "linked"
        git(self.repo, "worktree", "add", "--detach", str(linked))
        self.cfg.projects[0].worktree_policy = "all"
        self.scan("2026-10-01T13:00:00+00:00")
        (linked / "idea.py").write_text("idea = True\n")
        self.scan("2026-10-02T03:00:00+00:00")
        linked.rename(self.root / "renamed")
        self.scan("2026-10-02T03:05:00+00:00")
        project = self.bundle()["projects"][0]
        self.assertIn("gone", [e["kind"] for e in project["activity"]])
        self.assertTrue(any(t["gone"] for t in project["trees"]))

    def test_source_secret_blocks_and_config_values_never_persist(self):
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "feature.py").write_text(
            'password = "sensitive-fixture-password"\n'
            'CERT = """-----BEGIN PRIVATE KEY-----\n'
            'fixture-private-body-do-not-retain\n-----END PRIVATE KEY-----"""\n'
            'url = "https://alice:fixture-url-password@example.invalid"\n'
        )
        (self.repo / "settings.json").write_text('{"value": "fixture-config-do-not-retain"}')
        self.scan("2026-10-02T03:00:00+00:00")
        payload = "\n".join(self.conn.iterdump())
        for forbidden in ("sensitive-fixture-password", "fixture-private-body-do-not-retain",
                          "fixture-url-password", "fixture-config-do-not-retain"):
            self.assertNotIn(forbidden, payload)

    def test_committed_secret_and_subject_are_redacted(self):
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "feature.py").write_text('api_key = "fixture-raw-value"\nfeature = True\n')
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "password=fixture-commit-secret")
        self.scan("2026-10-02T12:00:00+00:00")
        payload = "\n".join(self.conn.iterdump())
        self.assertNotIn("fixture-commit-secret", payload)
        self.assertNotIn("fixture-raw-value", payload)

    def test_staged_unstaged_new_binary_symlink_and_budget_evidence(self):
        import os
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "README.md").write_text("staged version\n")
        git(self.repo, "add", "README.md")
        (self.repo / "README.md").write_text("working version\n")
        (self.repo / "new.py").write_text("fresh = True\n")
        (self.repo / "large.py").write_text("x" * 33000)
        (self.repo / "binary.py").write_bytes(b"\0binary")
        (self.root / "outside").write_text("must not enter evidence")
        (self.repo / "link.py").symlink_to(self.root / "outside")
        os.mkfifo(self.repo / "pipe.py")
        self.scan("2026-10-02T03:00:00+00:00")
        event = self.bundle()["projects"][0]["activity"][0]
        rows = {f["path"]: f for f in event["files"]}
        self.assertIn("staged version", rows["README.md"]["patch"])
        self.assertIn("working version", rows["README.md"]["patch"])
        self.assertIn("fresh", rows["new.py"]["patch"])
        for name in ("large.py", "binary.py", "link.py"):
            self.assertFalse(rows[name]["patch"])
        # Git omits FIFOs; the content reader must reject them immediately too.
        from xiaomao.change_evidence import file_text
        self.assertEqual(file_text(self.repo, "pipe.py", 100), (None, "not_regular"))
        (self.repo / "new.py").write_text("changed = True\n")
        with patch("xiaomao.ops.storage_over_budget", return_value=True):
            self.scan("2026-10-02T03:05:00+00:00")
        group = self.bundle()["projects"][0]
        self.assertIn("storage_budget", group["limitations"])
        self.assertFalse(any(f["patch"] for f in group["activity"][-1]["files"]))

    def test_company_remote_never_reaches_source_collection(self):
        git(self.repo, "remote", "add", "origin", "https://github.com/TheAiCommunity/unrelated.git")
        with patch("xiaomao.collect.collect_snapshot") as collect:
            self.scan("2026-10-02T03:00:00+00:00")
            collect.assert_not_called()
