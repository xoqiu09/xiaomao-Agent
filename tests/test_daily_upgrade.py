from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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
        from xiaomao.feature_render import render_details
        self.assertIn("Fix retry recovery", render_details(self.bundle(), {}))
        self.assertIn("已提交", body)
        self.assertNotIn("retry.py", body)
        self.assertNotIn("授权观察范围内无新变化", body)

    def test_unpushed_branch_commit_is_reminded_after_switching_to_main(self):
        from xiaomao.delivery_state import annotate_bundle
        from xiaomao.feature_render import render_plain_summary

        self.cfg.projects[0].display_name = "笔记"
        git(self.repo, "remote", "add", "origin", "https://github.com/xoqiu09/fixture.git")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.scan("2026-10-01T13:00:00+00:00")
        git(self.repo, "switch", "-c", "notes")
        (self.repo / "notes.txt").write_text("读书笔记\n")
        git(self.repo, "add", "notes.txt")
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-10-02T02:00:00+00:00"}):
            git(self.repo, "commit", "-m", "添加读书笔记")
        git(self.repo, "branch", "notes-copy")
        git(self.repo, "switch", "main")
        self.scan("2026-10-02T03:00:00+00:00")

        body = render_plain_summary(annotate_bundle(self.bundle()), {})
        self.assertIn("添加读书笔记  🟠 仅本机", body)
        self.assertIn("笔记 有 1 个提交还没推送", body)
        self.assertIn("明天从这里继续\n  1. 笔记：推送本地的 1 个提交", body)

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
        from xiaomao.daily_jobs import notification_text
        from xiaomao.feature_render import render_plain_summary

        self.cfg.projects[0].display_name = "笔记"
        self.scan("2026-10-01T13:00:00+00:00")
        missing = self.repo.rename(self.root / "temporarily-missing")
        self.assertEqual(self.scan("2026-10-02T03:00:00+00:00")["status"], "error")
        bundle = self.bundle()
        self.assertEqual(bundle["coverage"], "partial")
        self.assertTrue(bundle["projects"][0]["gaps"])
        body = render_plain_summary(bundle, {})
        with self.subTest(surface="晚报"):
            self.assertEqual(body.splitlines()[1], "今天没读到笔记的进度。")
            self.assertNotIn("今天没动：笔记", body)
        with self.subTest(surface="通知"):
            self.assertEqual(notification_text(bundle)[0], "今天没读到笔记的进度。")

        missing.rename(self.repo)
        self.assertIn(self.scan("2026-10-02T03:05:00+00:00")["status"], {"ok", "unchanged"})
        recovered = self.bundle()
        self.assertIn("今天没动：笔记", render_plain_summary(recovered, {}))
        self.assertEqual(notification_text(recovered)[0], "今天没有新的进展。")

    def test_daily_evidence_failure_does_not_claim_no_work(self):
        from xiaomao.daily_jobs import notification_text
        from xiaomao.feature_render import render_plain_summary

        self.cfg.projects[0].display_name = "笔记"
        self.scan("2026-10-01T13:00:00+00:00")
        with (patch("xiaomao.activity.record_snapshot", side_effect=OSError("read failed")),
              patch("xiaomao.store.utc_now", return_value="2026-10-02T03:00:00+00:00")):
            self.assertEqual(self.scan("2026-10-02T03:00:00+00:00")["status"], "unchanged")
        failed = self.bundle()
        self.assertIn("website-main: 日报证据采集失败", failed["projects"][0]["gaps"])
        with self.subTest(surface="晚报"):
            body = render_plain_summary(failed, {})
            self.assertEqual(body.splitlines()[1], "今天没读到笔记的进度。")
            self.assertNotIn("今天没动：笔记", body)
        with self.subTest(surface="通知"):
            self.assertEqual(notification_text(failed)[0], "今天没读到笔记的进度。")

        check = self.conn.execute("SELECT check_id,results_json FROM daily_checks ORDER BY rowid DESC LIMIT 1").fetchone()
        old_results = json.loads(check["results_json"])
        del old_results[0]["daily_evidence_ok"]
        self.conn.execute("UPDATE daily_checks SET results_json=? WHERE check_id=?",
                          (json.dumps(old_results), check["check_id"]))
        self.assertEqual(notification_text(self.bundle())[0], "今天没读到笔记的进度。")

        self.assertEqual(self.scan("2026-10-02T03:05:00+00:00")["status"], "unchanged")
        self.scan("2026-10-02T14:00:00+00:00")
        recovered = self.bundle()
        self.assertIn("今天没动：笔记", render_plain_summary(recovered, {}))
        self.assertEqual(notification_text(recovered)[0], "今天没有新的进展。")

    def test_late_scan_counts_the_commit_without_claiming_unread_progress(self):
        from xiaomao.daily_jobs import notification_text
        from xiaomao.feature_render import render_plain_summary

        self.cfg.projects[0].display_name = "笔记"
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "notes.txt").write_text("读书笔记\n")
        git(self.repo, "add", "notes.txt")
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-10-02T03:00:00+00:00"}):
            git(self.repo, "commit", "-m", "整理读书笔记")
        today = "2026-10-02"
        self.assertEqual(notification_text(self.bundle(today))[0], "今天没读到笔记的进度。")
        self.scan("2026-10-02T14:00:00+00:00")

        regenerated = self.bundle(today)
        body = render_plain_summary(regenerated, {})
        self.assertIn("整理读书笔记", body)
        self.assertTrue(any("采集缺口" in gap for gap in regenerated["projects"][0]["gaps"]))
        with self.subTest(surface="晚报"):
            self.assertEqual(body.splitlines()[1], "今天 1 个项目有进展。")
        with self.subTest(surface="通知"):
            self.assertEqual(notification_text(regenerated)[0], "今天 1 个项目有进展。")

    def test_many_unread_projects_leave_room_for_push_reminder_in_notification(self):
        from xiaomao.daily_jobs import macos_notification, notification_text

        bundle = {"projects": [
            {"display_name": f"没有读到进度的个人项目编号{i:02d}", "scan_succeeded": False, "changed": False}
            for i in range(12)
        ] + [{"display_name": "笔记", "changed": True,
              "delivery_state": {"has_remote": True, "unpushed": ["abc"]}}]}
        message = notification_text(bundle)
        with patch("xiaomao.daily_jobs.subprocess.run", return_value=Mock(returncode=0)) as run:
            self.assertTrue(macos_notification("2026-10-02", "", message))
        subtitle = run.call_args.args[0][-2]
        with self.subTest(part="读取失败"):
            self.assertIn("没读到 12 个项目的进度", subtitle)
        with self.subTest(part="推送提醒"):
            self.assertIn("笔记 有 1 个提交还没推送", subtitle)

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
