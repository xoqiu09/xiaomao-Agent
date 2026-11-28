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

    def saved_report(self, day, observed_at):
        from datetime import datetime
        from xiaomao.config import save_config
        from xiaomao.daily_jobs import run_daily

        save_config(self.cfg)
        with patch("xiaomao.collect.utc_now", return_value=observed_at):
            path, = run_daily(self.cfg, date=day, now=datetime.fromisoformat(observed_at))
        return path.read_text()

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
        from datetime import datetime
        from xiaomao.daily import build_bundle
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

        bundle = build_bundle(self.cfg, self.conn, date="2026-10-02",
                              now=datetime.fromisoformat("2026-10-02T13:35:00+00:00"))
        body = render_plain_summary(annotate_bundle(bundle), {})
        self.assertIn("添加读书笔记  🟠 仅本机", body)
        self.assertIn("笔记 有 1 个提交还没推送", body)
        self.assertIn("明天从这里继续\n  1. 笔记：推送本地的 1 个提交", body)

    def test_backfill_keeps_unpushed_counts_with_their_observation_day(self):
        from xiaomao.ask import build_packet, recent_bundles

        self.cfg.timezone = "Asia/Taipei"
        self.cfg.daily_time = "21:30"
        git(self.repo, "remote", "add", "origin", "https://github.com/xoqiu09/fixture.git")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.scan("2026-11-25T13:29:00+00:00")
        self.conn.commit()
        (self.repo / "README.md").write_text("new on the 26th\n")
        git(self.repo, "add", "README.md")
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-11-26T01:00:00+00:00"}):
            git(self.repo, "commit", "-m", "Work from the 26th")
        sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()

        late = self.saved_report("2026-11-25", "2026-11-26T02:00:00+00:00")
        self.assertNotIn("1 个提交还没推送", late)
        self.assertNotIn("推送本地的", late)
        self.assertIn("当天截止时的未推送数量没有记录", late)
        self.assertIn("2026-11-26 10:00", late)
        detail = Path(self.cfg.home) / "reports/daily-evidence/2026-11-25.txt"
        self.assertIn("补报查询时间 2026-11-26T02:00:00+00:00 · 未推送提交 1 个", detail.read_text())
        current = self.saved_report("2026-11-26", "2026-11-26T13:35:00+00:00")
        self.assertIn("1 个提交还没推送", current)

        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        rebuilt = self.saved_report("2026-11-26", "2026-11-27T02:00:00+00:00")
        self.assertIn("1 个提交还没推送", rebuilt)
        bundles = recent_bundles(self.conn, self.cfg, days=2, until="2026-11-26")
        days = build_packet(bundles)
        self.assertNotIn("1 个提交还没推送", days[0]["summary"])
        self.assertIsNone(days[0]["status"][0]["unpushed_commits"])
        self.assertEqual(days[1]["status"][0]["unpushed_commits"], 1)

        legacy_info = bundles[0]["projects"][0]["delivery_state"]
        del legacy_info["unpushed_check"], legacy_info["unpushed_window_at"]
        legacy_info["has_remote"] = True
        legacy_info["unpushed"] = [sha]
        legacy_day, = build_packet(bundles[:1])
        self.assertIsNone(legacy_day["status"][0]["unpushed_commits"])
        self.assertEqual(legacy_day["next_steps"], [])

    def test_same_night_and_next_day_rebuilds_keep_the_saved_zero(self):
        from xiaomao.ask import build_packet, recent_bundles

        day = "2026-11-25"
        git(self.repo, "remote", "add", "origin", "https://github.com/xoqiu09/fixture.git")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.scan("2026-11-25T13:29:00+00:00")
        self.conn.commit()
        detail = Path(self.cfg.home) / f"reports/daily-evidence/{day}.txt"
        self.saved_report(day, "2026-11-25T13:35:00+00:00")
        self.assertIn("未推送提交 0 个", detail.read_text())
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-11-25T14:00:00+00:00"}):
            git(self.repo, "commit", "--allow-empty", "-m", "Commit after cutoff")

        for at in ("2026-11-25T15:00:00+00:00", "2026-11-26T02:00:00+00:00"):
            with self.subTest(at=at):
                body = self.saved_report(day, at)
                self.assertNotIn("1 个提交还没推送", body)
                self.assertIn("未推送提交 0 个（当日记录于 2026-11-25T13:35:00+00:00）", detail.read_text())
                self.assertIn(f"补报查询时间 {at} · 未推送提交 1 个", detail.read_text())
                packet, = build_packet(recent_bundles(self.conn, self.cfg, days=1, until=day))
                self.assertEqual(packet["status"][0]["unpushed_commits"], 0)
                self.assertEqual(packet["next_steps"], [])

    def test_backfill_reports_original_commit_state_and_dates_the_new_state(self):
        from xiaomao.ask import build_packet, recent_bundles

        day = "2026-11-25"
        git(self.repo, "remote", "add", "origin", "https://github.com/xoqiu09/fixture.git")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.scan("2026-11-24T13:29:00+00:00")
        self.conn.commit()
        (self.repo / "notes.txt").write_text("a local change\n")
        git(self.repo, "add", "notes.txt")
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-11-25T10:00:00+00:00"}):
            git(self.repo, "commit", "-m", "Add notes")
        sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.saved_report(day, "2026-11-25T13:28:00+00:00")
        self.saved_report(day, "2026-11-25T13:35:00+00:00")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")

        at = "2026-11-26T02:00:00+00:00"
        body = self.saved_report(day, at)
        detail = (Path(self.cfg.home) / f"reports/daily-evidence/{day}.txt").read_text()
        packet, = build_packet(recent_bundles(self.conn, self.cfg, days=1, until=day))
        self.assertIn("1 个提交还没推送", body)
        self.assertNotIn("🟢 已合并", body)
        self.assertIn("🟠 仅本机", body)
        self.assertIn(f"{sha[:12]} → local", detail)
        self.assertIn(f"补报查询时间 {at} · 未推送提交 0 个\n      {sha[:12]} → merged", detail)
        self.assertEqual(packet["status"][0]["unpushed_commits"], 1)
        self.assertEqual(packet["projects"][0]["commits"][0]["where"], "🟠 仅本机")
        recheck = packet["status"][0]["delivery_recheck"]
        self.assertEqual((recheck["at"], recheck["unpushed_commits"]), (at, 0))
        self.assertEqual(recheck["commits"][0]["where"], "🟢 已合并")

    def test_unpushed_count_distinguishes_a_lower_bound_from_no_result(self):
        from xiaomao.ask import build_packet, recent_bundles

        git(self.repo, "remote", "add", "origin", "https://github.com/xoqiu09/fixture.git")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-11-25T10:00:00+00:00"}):
            git(self.repo, "commit", "--allow-empty", "-m", "Local change")
        sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        (self.repo / "draft.txt").write_text("work in progress\n")
        self.scan("2026-11-25T13:29:00+00:00")
        self.conn.commit()
        for day, found, expected in (("2026-11-25", None, None), ("2026-11-26", [sha], 1)):
            with self.subTest(day=day):
                at = f"{day}T13:35:00+00:00"
                with patch("xiaomao.delivery_state.unpushed_commits",
                           side_effect=lambda _path, _view, *, other_branches: None if other_branches else found):
                    body = self.saved_report(day, at)
                evidence = (Path(self.cfg.home) / f"reports/daily-evidence/{day}.txt").read_text()
                packet, = build_packet(recent_bundles(self.conn, self.cfg, days=1, until=day))
                status = packet["status"][0]
                if found is None:
                    self.assertIsNone(status["unpushed_commits"])
                    self.assertIn("未推送数量没查到", body)
                    self.assertIn("未推送数量没查到", evidence)
                    self.assertNotIn("未推送提交 0 个", evidence)
                else:
                    self.assertIn("至少 1 个提交还没推送", body)
                    self.assertIn("未推送提交 至少 1 个", evidence)
                    self.assertTrue(status.get("unpushed_at_least"))
                self.assertEqual(status["unpushed_commits"], expected)

    def test_backfill_git_failure_does_not_report_a_current_zero(self):
        from xiaomao.ask import build_packet, recent_bundles
        from xiaomao.git_readonly import GitReadError

        day, at = "2026-11-25", "2026-11-26T02:00:00+00:00"
        git(self.repo, "remote", "add", "origin", "https://github.com/xoqiu09/fixture.git")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.scan("2026-11-25T13:25:00+00:00")
        self.conn.commit()
        with patch("xiaomao.delivery_state.remote_view", side_effect=GitReadError("read failed")):
            self.saved_report(day, at)
        detail = (Path(self.cfg.home) / f"reports/daily-evidence/{day}.txt").read_text()
        packet, = build_packet(recent_bundles(self.conn, self.cfg, days=1, until=day))
        self.assertIn(f"补报查询时间 {at} · 未推送数量没查到", detail)
        self.assertIsNone(packet["status"][0]["unpushed_commits"])
        self.assertIsNone(packet["status"][0]["delivery_recheck"]["unpushed_commits"])

    def bundle(self, date="2026-10-02"):
        from datetime import datetime, timezone
        from xiaomao.daily import build_bundle
        return build_bundle(self.cfg, self.conn, date=date, now=datetime(2026, 10, 3, tzinfo=timezone.utc))

    def test_switch_at_same_commit_is_evidence_only(self):
        from xiaomao.feature_daily import project_units
        from xiaomao.feature_render import render_details, render_plain_summary

        self.scan("2026-10-01T13:00:00+00:00")
        git(self.repo, "switch", "-c", "same-commit")
        self.scan("2026-10-02T03:00:00+00:00")

        bundle = self.bundle()
        body = render_plain_summary(bundle, {})
        self.assertEqual(body.splitlines()[1], "今天没有新的进展。")
        self.assertNotIn("有过改动", body)
        detail = render_details(bundle, {})
        self.assertIn("同一提交上的分支切换", detail)
        self.assertIn("main → same-commit", detail)
        self.assertEqual(project_units(bundle["projects"][0]), [])

    def test_switch_with_untracked_file_beyond_evidence_limit_counts_as_progress(self):
        from xiaomao.feature_render import render_plain_summary

        for number in range(200):
            (self.repo / f"draft-{number:03}.txt").write_text("draft\n")
        self.scan("2026-10-01T13:00:00+00:00")
        git(self.repo, "switch", "-c", "same-commit")
        (self.repo / "draft-200.txt").write_text("another draft\n")
        self.scan("2026-10-02T03:00:00+00:00")

        bundle = self.bundle()
        project, = bundle["projects"]
        ongoing, = project["ongoing"]
        self.assertEqual(ongoing["untracked"], 201)
        self.assertEqual(len(ongoing["files"]), 200)
        self.assertEqual(render_plain_summary(bundle, {}).splitlines()[1], "今天 1 个项目有进展。")
        event, = project["activity"]
        self.assertEqual((event["kind"], event["paths"]), ("workspace", []))

    def test_switch_before_first_commit_is_evidence_only(self):
        from xiaomao.feature_render import render_details, render_plain_summary

        self.repo = init_repo(self.root / "empty", initial_commit=False)
        self.cfg = website_fixture_config(self.root / "home", self.repo)
        self.assertEqual(self.scan("2026-10-01T13:00:00+00:00")["status"], "ok")
        git(self.repo, "switch", "-c", "demo")
        self.assertEqual(self.scan("2026-10-02T03:00:00+00:00")["status"], "ok")

        bundle = self.bundle()
        self.assertEqual(render_plain_summary(bundle, {}).splitlines()[1], "今天没有新的进展。")
        detail = render_details(bundle, {})
        self.assertIn("首次提交前的分支切换", detail)
        self.assertIn("main → demo", detail)

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

    def test_replaced_checkout_resumes_recording_after_losing_all_previous_commits(self):
        remote = "https://github.com/xoqiu09/fixture.git"
        git(self.repo, "remote", "add", "origin", remote)
        old = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.scan("2026-10-01T13:25:00+00:00")

        self.repo.rename(self.root / "original")
        init_repo(self.repo, initial_commit=False)
        git(self.repo, "remote", "add", "origin", remote)
        git(self.repo, "commit", "--allow-empty", "-m", "Replacement starting point")
        self.assertNotEqual(git(self.repo, "cat-file", "-e", old, check=False).returncode, 0)
        self.scan("2026-10-02T03:00:00+00:00")

        git(self.repo, "commit", "--allow-empty", "-m", "New work after replacement")
        new = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.scan("2026-10-02T03:05:00+00:00")

        project = self.bundle()["projects"][0]
        self.assertEqual([commit["sha"] for commit in project["commits"]], [new])
        self.assertIn("commit_history_unavailable: missing previous commit", project["limitations"])

    def test_replaced_checkout_keeps_the_previous_commits_it_can_still_read(self):
        remote = "https://github.com/xoqiu09/fixture.git"
        git(self.repo, "remote", "add", "origin", remote)
        git(self.repo, "switch", "-c", "old-side-branch")
        git(self.repo, "commit", "--allow-empty", "-m", "Old side work")
        lost = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        git(self.repo, "switch", "main")
        self.scan("2026-10-01T13:25:00+00:00")

        original = self.root / "original"
        self.repo.rename(original)
        git(self.root, "clone", "--no-local", "--single-branch", str(original), str(self.repo))
        git(self.repo, "remote", "set-url", "origin", remote)
        self.assertNotEqual(git(self.repo, "cat-file", "-e", lost, check=False).returncode, 0)
        git(self.repo, "commit", "--allow-empty", "-m", "New main work")
        new = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.scan("2026-10-02T03:00:00+00:00")

        project = self.bundle()["projects"][0]
        self.assertEqual([commit["sha"] for commit in project["commits"]], [new])

    def test_missing_previous_commit_is_recognized_with_a_git_warning(self):
        from xiaomao.git_readonly import run_git

        old = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        remote = "https://github.com/xoqiu09/fixture.git"
        git(self.repo, "remote", "add", "origin", remote)
        self.scan("2026-10-01T13:25:00+00:00")

        self.repo.rename(self.root / "original")
        init_repo(self.repo, initial_commit=False)
        git(self.repo, "remote", "add", "origin", remote)
        git(self.repo, "commit", "--allow-empty", "-m", "Replacement starting point")

        def warn_on_missing_check(path, *args, **kwargs):
            result = run_git(path, *args, **kwargs)
            if args[:3] == ("rev-list", "--no-walk", "--missing=print"):
                self.assertEqual(result.returncode, 0)
                self.assertIn(f"?{old}", result.stdout.splitlines())
                result.stderr += "warning: could not resolve a temporary directory\n"
            return result

        with patch("xiaomao.activity.run_git", side_effect=warn_on_missing_check):
            self.scan("2026-10-02T03:00:00+00:00")
            git(self.repo, "commit", "--allow-empty", "-m", "New work after replacement")
            new = git(self.repo, "rev-parse", "HEAD").stdout.strip()
            self.scan("2026-10-02T03:05:00+00:00")

        project = self.bundle()["projects"][0]
        self.assertEqual([commit["sha"] for commit in project["commits"]], [new])

    def test_empty_replacement_does_not_count_restored_history_as_new_work(self):
        remote = "https://github.com/xoqiu09/fixture.git"
        git(self.repo, "remote", "add", "origin", remote)
        old = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.scan("2026-10-01T13:25:00+00:00")

        original = self.root / "original"
        self.repo.rename(original)
        init_repo(self.repo, initial_commit=False)
        git(self.repo, "remote", "add", "origin", remote)
        self.scan("2026-10-02T03:00:00+00:00")
        git(self.repo, "fetch", str(original), "main")
        git(self.repo, "reset", "--hard", "FETCH_HEAD")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").stdout.strip(), old)
        self.scan("2026-10-02T03:05:00+00:00")

        self.assertEqual([commit["sha"] for commit in self.bundle()["projects"][0]["commits"]], [])

    def test_temporary_history_read_failure_keeps_the_previous_commits_for_retry(self):
        from subprocess import CompletedProcess
        from xiaomao.git_readonly import run_git

        self.scan("2026-10-01T13:25:00+00:00")
        git(self.repo, "commit", "--allow-empty", "-m", "Work to recover on retry")
        new = git(self.repo, "rev-parse", "HEAD").stdout.strip()

        def fail_history_walk(path, *args, **kwargs):
            if args[:2] == ("rev-list", "--reverse"):
                return CompletedProcess(args, 128, "", "temporary read failure")
            return run_git(path, *args, **kwargs)

        with patch("xiaomao.activity.run_git", side_effect=fail_history_walk):
            self.scan("2026-10-02T03:00:00+00:00")
        self.assertEqual(self.bundle()["projects"][0]["commits"], [])

        self.scan("2026-10-02T03:05:00+00:00")
        project = self.bundle()["projects"][0]
        self.assertEqual([commit["sha"] for commit in project["commits"]], [new])

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

    def test_discarded_draft_is_not_covered_by_an_unrelated_commit(self):
        from xiaomao.feature_daily import project_units

        self.scan("2026-10-01T13:00:00+00:00")
        draft = self.repo / "draft.py"
        draft.write_text("draft = True\n")
        self.scan("2026-10-02T03:00:00+00:00")
        draft.unlink()
        (self.repo / "guide.md").write_text("# Guide\n")
        git(self.repo, "add", "guide.md")
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-10-02T03:02:00+00:00"}):
            git(self.repo, "commit", "-m", "Add guide")
        self.scan("2026-10-02T03:05:00+00:00")

        project = self.bundle()["projects"][0]
        disappearance = next(event for event in project["activity"] if event["kind"] == "resolved")
        self.assertEqual(disappearance["paths"], ["draft.py"])
        self.assertEqual(disappearance["covered_by_commits"], [])
        units = {(unit["path"], unit["kind"], unit["origin"]) for unit in project_units(project)}
        self.assertIn(("draft.py", "resolved", "activity"), units)
        self.assertIn(("guide.md", "documentation", "commit"), units)

    def test_deleted_draft_is_not_covered_by_a_commit_retaining_an_empty_file(self):
        draft = self.repo / "draft.py"
        draft.write_text("draft = True\n")
        git(self.repo, "add", "draft.py")
        git(self.repo, "commit", "-m", "Track draft")
        self.scan("2026-10-01T13:00:00+00:00")
        draft.unlink()
        self.scan("2026-10-02T03:00:00+00:00")
        draft.write_bytes(b"")
        git(self.repo, "add", "draft.py")
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-10-02T03:02:00+00:00"}):
            git(self.repo, "commit", "-m", "Keep an empty draft")
        self.scan("2026-10-02T03:05:00+00:00")

        project = self.bundle()["projects"][0]
        self.assertEqual(len(project["commits"]), 1)
        event = next(event for event in project["activity"] if event["kind"] == "resolved")
        self.assertEqual([event["covered_by_commits"], event["files"][0]["covered_by_commits"]], [[], []])

    def test_draft_with_crlf_is_covered_by_a_commit_with_identical_bytes(self):
        git(self.repo, "config", "core.autocrlf", "false")
        self.scan("2026-10-01T13:00:00+00:00")
        draft = self.repo / "draft.py"
        content = b"draft = True\r\nprint(draft)\r\n"
        draft.write_bytes(content)
        self.scan("2026-10-02T03:00:00+00:00")
        git(self.repo, "add", "draft.py")
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-10-02T03:02:00+00:00"}):
            git(self.repo, "commit", "-m", "Commit draft with CRLF")
        self.scan("2026-10-02T03:05:00+00:00")

        project = self.bundle()["projects"][0]
        commit, = project["commits"]
        event = next(event for event in project["activity"] if event["kind"] == "workspace")
        self.assertEqual([event["covered_by_commits"], event["files"][0]["covered_by_commits"]],
                         [[commit["sha"]], [commit["sha"]]])

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
