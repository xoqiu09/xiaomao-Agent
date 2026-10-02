import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.helpers import git, init_repo, website_fixture_config
from tests.feature_helpers import response
from xiaomao.collect import scan_authorized
from xiaomao.config import ProjectSpec, WorktreeSpec, save_config
from xiaomao.daily import build_bundle
from xiaomao.store import open_db


class DailyJobTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = init_repo(self.root / "one")
        self.cfg = website_fixture_config(self.root / "home", self.repo)
        self.repos = [self.repo]
        for i in (2, 3):
            repo = init_repo(self.root / str(i))
            self.repos.append(repo)
            self.cfg.projects.append(ProjectSpec(
                str(i), f"项目{i}", str(repo), [WorktreeSpec(f"tree-{i}", str(repo))],
            ))
        save_config(self.cfg)
        self.db = self.root / "home/xiaomao.sqlite"
        with open_db(self.db) as conn, patch("xiaomao.collect.utc_now", return_value="2026-10-01T13:00:00+00:00"):
            scan_authorized(conn, self.cfg)
        for i, repo in enumerate(self.repos[1:], 2):
            (repo / "feature.py").write_text(f"feature = {i}\n")
            git(repo, "add", "feature.py")
            git(repo, "commit", "-m", f"Feature {i}")
        with open_db(self.db) as conn, patch("xiaomao.collect.utc_now", return_value="2026-10-02T03:00:00+00:00"):
            scan_authorized(conn, self.cfg)
            self.bundle = build_bundle(self.cfg, conn, date="2026-10-02",
                                       now=datetime(2026, 10, 2, 14, tzinfo=timezone.utc))

    def test_second_and_third_projects_get_model_outside_database_transaction(self):
        from xiaomao.daily_jobs import summarize_projects
        captured = []
        db, cfg = self.db, self.cfg
        class Client:
            def generate_json(self, **kwargs):
                with sqlite3.connect(db, timeout=0) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.rollback()
                from xiaomao.lock import ScanLock
                with ScanLock(Path(cfg.home) / "xiaomao.lock"):
                    pass
                packet = json.loads(kwargs["user"])
                captured.append(packet["project_id"])
                return response(packet)
            def stop(self, _):
                raise AssertionError("must not stop a real model or process")
        notes = summarize_projects(self.cfg, self.bundle, Client())
        self.assertEqual(captured, ["2", "3"])
        self.assertEqual(sum(bool(v.get("accepted")) for v in notes.values()), 2)

    def test_bad_model_claims_fall_back_to_rule_report(self):
        from xiaomao.daily_jobs import summarize_projects
        from xiaomao.daily import render_bundle
        class BadClient:
            def generate_json(self, **kwargs):
                return {"json": {"bullets": [{"text": "已部署", "evidence_ids": ["invented"]}]}}
        notes = summarize_projects(self.cfg, self.bundle, BadClient())
        self.assertFalse(any(n.get("bullets") for n in notes.values()))
        text = render_bundle(self.bundle, notes)
        from xiaomao.feature_render import render_details
        self.assertIn("Feature 2", render_details(self.bundle, notes))
        self.assertIn("功能影响待确认", text)
        self.assertIn("模型解读缺失", text)

    def test_notification_once_even_on_repeated_generation_or_failure(self):
        from xiaomao.daily_jobs import notify_once
        calls = []
        with open_db(self.db) as conn:
            notify_once(conn, self.cfg, self.bundle, lambda *args: calls.append(args) or False)
            notify_once(conn, self.cfg, self.bundle, lambda *args: calls.append(args) or True)
        self.assertEqual(len(calls), 1)

    def test_due_windows_are_fixed_and_catch_up_without_duplicates(self):
        from xiaomao.daily_jobs import due_dates, persist_window
        now = datetime(2026, 10, 3, 14, tzinfo=timezone.utc)
        with open_db(self.db) as conn:
            dates = due_dates(self.cfg, conn, now)
            self.assertEqual(dates, ["2026-10-01", "2026-10-02", "2026-10-03"])
            persist_window(conn, self.bundle)
            self.assertNotIn("2026-10-02", due_dates(self.cfg, conn, now))

    def test_scheduled_descriptor_checks_missed_reports_without_restarting_scan(self):
        from xiaomao.schedule import daily_plist_payload
        payload = daily_plist_payload(home=Path(self.cfg.home), log_dir=Path(self.cfg.home) / "logs")
        self.assertEqual(payload.get("StartInterval"), 300)
        self.assertTrue(payload["RunAtLoad"])

    def test_run_publishes_same_packet_and_cache_without_scan_lock(self):
        from xiaomao.daily_jobs import run_daily
        from xiaomao.report_access import read_bound_report
        calls = []
        cfg = self.cfg
        class Client:
            def generate_json(self, **kwargs):
                packet = json.loads(kwargs["user"])
                calls.append(packet["project_id"])
                from xiaomao.lock import ScanLock
                with ScanLock(Path(cfg.home) / "xiaomao.lock"), sqlite3.connect(self_db, timeout=0) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.rollback()
                return response(packet)
        self_db = self.db
        now = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
        with patch("xiaomao.collect.utc_now", return_value=now.isoformat()):
            paths = run_daily(cfg, date="2026-10-02", with_model=True, now=now,
                              client_factory=lambda _: Client())
            run_daily(cfg, date="2026-10-02", with_model=True, now=now,
                      client_factory=lambda _: Client())
        self.assertEqual(calls, ["2", "3"])
        body = read_bound_report(paths[0], Path(cfg.home), cfg, "daily")
        from xiaomao.report_access import read_daily_details
        self.assertIn("Feature 2", read_daily_details(paths[0], Path(cfg.home), cfg)[1])
        self.assertNotIn("feature.py", body)
        menu = Path(cfg.home) / "reports/briefing/menu-2026-10-02.txt"
        self.assertIn("增加了功能逻辑", read_bound_report(menu, Path(cfg.home), cfg, "briefing"))
        self.assertEqual(json.loads(paths[0].with_suffix(".json").read_text())["evidence_hash"],
                         json.loads(menu.with_suffix(".json").read_text())["evidence_hash"])

    def test_swiftbar_rejects_briefing_from_previous_scope(self):
        from xiaomao.menu_briefing import write_daily_briefing
        from xiaomao.swiftbar import render_menu
        write_daily_briefing(self.cfg, self.bundle, {})
        self.cfg.projects = self.cfg.projects[:1]
        save_config(self.cfg)
        self.assertNotIn("Feature 2", render_menu(Path(self.cfg.home)))

    def test_model_failure_is_delivered_and_preview_never_notifies(self):
        from xiaomao.daily_jobs import run_daily
        calls = []
        class BadClient:
            def generate_json(self, **kwargs):
                raise RuntimeError("fixture failure")
        with patch("xiaomao.collect.utc_now", return_value="2026-10-02T10:00:00+00:00"):
            paths = run_daily(self.cfg, date="2026-10-02", now=datetime(2026, 10, 2, 10, tzinfo=timezone.utc),
                              scheduled=True, client_factory=lambda _: BadClient(),
                              notifier=lambda *args: calls.append(args))
        self.assertFalse(calls)
        self.assertIn("模型解读缺失", paths[0].read_text())

    def test_late_commit_repairs_report_without_second_notification(self):
        from xiaomao.daily_jobs import persist_window, due_dates, notify_once
        calls = []
        now = datetime(2026, 10, 3, 14, tzinfo=timezone.utc)
        with open_db(self.db) as conn, patch("xiaomao.daily_jobs.utc_now", return_value="2026-10-02T14:00:00+00:00"):
            persist_window(conn, self.bundle)
            notify_once(conn, self.cfg, self.bundle, lambda *a: calls.append(a) or True)
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-10-02T12:00:00+00:00"}):
            (self.repo / "late.py").write_text("late = True\n")
            git(self.repo, "add", ".")
            git(self.repo, "commit", "-m", "Offline commit")
        with open_db(self.db) as conn, patch("xiaomao.collect.utc_now", return_value=now.isoformat()):
            scan_authorized(conn, self.cfg)
            self.assertIn("2026-10-02", due_dates(self.cfg, conn, now))
            repaired = build_bundle(self.cfg, conn, date="2026-10-02", now=now)
            self.assertTrue(any(c["subject"] == "Offline commit" for c in repaired["projects"][0]["commits"]))
            notify_once(conn, self.cfg, repaired, lambda *a: calls.append(a) or True)
        self.assertEqual(len(calls), 1)

    def test_cli_coverage_and_config_and_schema_upgrade(self):
        import io
        from contextlib import redirect_stdout
        from xiaomao.cli import main
        from xiaomao.config import load_config
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--home", self.cfg.home, "projects", "coverage", "--date", "2026-10-02"]), 0)
        self.assertEqual(len(json.loads(output.getvalue())["projects"]), 3)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--home", self.cfg.home, "projects", "configure", "--time", "21:30",
                                   "--timezone", "Asia/Taipei", "--project", "website", "--worktrees", "all"]), 0)
        self.assertEqual(load_config(Path(self.cfg.home)).projects[0].worktree_policy, "all")
        with open_db(self.db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            conn.execute("UPDATE meta SET value='2' WHERE key='schema'")
        with open_db(self.db) as conn:
            self.assertEqual(conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], "4")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0], count)

    def test_regenerating_preserves_old_report_and_metadata(self):
        from xiaomao.daily_jobs import run_daily
        folder = Path(self.cfg.home) / "reports/daily"
        folder.mkdir(parents=True)
        old = folder / "2026-10-02.txt"
        old.write_text("旧版日报正文\n")
        old.with_suffix(".json").write_text('{"old": true}\n')
        with patch("xiaomao.collect.utc_now", return_value="2026-10-02T14:00:00+00:00"):
            run_daily(self.cfg, date="2026-10-02", now=datetime(2026, 10, 2, 14, tzinfo=timezone.utc))
        backups = list((folder / "history").rglob("*.txt"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "旧版日报正文\n")
        self.assertEqual(backups[0].with_suffix(".json").read_text(), '{"old": true}\n')

    def test_detail_and_menu_reject_mismatched_versions_and_revoked_scope(self):
        from xiaomao.daily_jobs import run_daily
        from xiaomao.report_access import read_daily_details, read_bound_report
        from xiaomao.swiftbar import render_menu
        now = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
        with patch("xiaomao.collect.utc_now", return_value=now.isoformat()):
            main = run_daily(self.cfg, date="2026-10-02", now=now)[0]
        detail, _ = read_daily_details(main, Path(self.cfg.home), self.cfg)
        menu = Path(self.cfg.home) / "reports/briefing/menu-2026-10-02.txt"
        self.assertIn("查看日报依据", render_menu(Path(self.cfg.home)))
        metadata = json.loads(detail.with_suffix(".json").read_text())
        metadata["interpretation_hash"] = "wrong-version"
        detail.with_suffix(".json").write_text(json.dumps(metadata))
        with self.assertRaises(ValueError):
            read_daily_details(main, Path(self.cfg.home), self.cfg)
        self.assertNotIn("查看日报依据 |", render_menu(Path(self.cfg.home)))
        menu_meta = json.loads(menu.with_suffix(".json").read_text())
        menu_meta["interpretation_hash"] = "wrong-version"
        menu.with_suffix(".json").write_text(json.dumps(menu_meta))
        with self.assertRaises(ValueError):
            read_bound_report(menu, Path(self.cfg.home), self.cfg, "briefing")
        self.cfg.projects = self.cfg.projects[:1]
        save_config(self.cfg)
        with self.assertRaises(ValueError):
            read_bound_report(detail, Path(self.cfg.home), self.cfg, "daily-evidence")

    def test_context_version_invalidates_model_cache(self):
        from copy import deepcopy
        from xiaomao.daily_jobs import summarize_projects
        calls = []
        class Client:
            def generate_json(self, **kwargs):
                packet = json.loads(kwargs["user"])
                calls.append(packet["project_id"])
                return response(packet)
        client = Client()
        summarize_projects(self.cfg, self.bundle, client)
        summarize_projects(self.cfg, self.bundle, client)
        self.assertEqual(len(calls), 2)
        changed = deepcopy(self.bundle)
        changed["projects"][1]["feature_contexts"]["test-version"] = {"purpose": "changed"}
        summarize_projects(self.cfg, changed, client)
        self.assertEqual(len(calls), 3)
