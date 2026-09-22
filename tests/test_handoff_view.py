from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.helpers import init_repo, website_fixture_config
from xiaomao.cli import main
from xiaomao.config import save_config
from xiaomao.handoff_view import inspect_handoff
from xiaomao.swiftbar import render_menu


class HandoffViewTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = init_repo(self.root / "repo")
        self.home = self.root / "data home"
        self.cfg = website_fixture_config(self.home, self.repo)
        save_config(self.cfg)
        self.call("init")

    def call(self, *args, expected=0):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--home", str(self.home), *args])
        self.assertEqual(code, expected, out.getvalue() + err.getvalue())
        return out.getvalue()

    def report(self):
        self.call("scan", "--project", "website")
        return Path(self.call("handoff", "--project", "website").strip())

    def change_meta(self, path, update):
        meta_path = path.with_suffix(".json")
        meta = json.loads(meta_path.read_text())
        update(meta)
        meta_path.write_text(json.dumps(meta))

    def test_fresh_cli_and_menu_explain_unknowns_without_scan_model_or_write(self):
        path = self.report()
        db_hash = hashlib.sha256((self.home / "xiaomao.sqlite").read_bytes()).hexdigest()
        with patch("xiaomao.cli.scan_authorized", side_effect=AssertionError("scan invoked")), \
             patch("xiaomao.cli._maybe_model_note", side_effect=AssertionError("model invoked")), \
             patch("xiaomao.cli.open_db", side_effect=AssertionError("write connection invoked")):
            text = self.call("latest", "handoff", "--project", "website", "--print")
            menu = render_menu(self.home)
        self.assertIn("资料状态：交接可读", text)
        self.assertIn("观察来源：website-main / obs_", text)
        self.assertIn("未核实项", text)
        self.assertIn("部署 / 已验收：unknown", text)
        self.assertIn(str(path), text)
        self.assertIn("查看交接与未核实项：website | bash=", menu)
        self.assertIn("测试：unknown", menu)
        self.assertEqual(hashlib.sha256((self.home / "xiaomao.sqlite").read_bytes()).hexdigest(), db_hash)

    def test_missing_and_empty_data_are_not_success(self):
        self.call("latest", "handoff", "--project", "website", expected=2)
        self.call("handoff", "--project", "website", expected=3)
        view = inspect_handoff(self.home, "website")
        self.assertEqual(view.status, "尚无资料")
        self.assertNotEqual(view.exit_code, 0)

    def test_stale_report_not_freshened_by_touch_or_new_success(self):
        path = self.report()
        later = datetime.now(timezone.utc) + timedelta(minutes=12)
        os.utime(path, (later.timestamp(), later.timestamp()))
        with sqlite3.connect(self.home / "xiaomao.sqlite") as conn:
            conn.execute("UPDATE scan_runs SET finished_at=?", (later.isoformat(),))
        view = inspect_handoff(self.home, "website", now=later)
        self.assertEqual(view.status, "信息已过期")
        self.assertEqual(view.exit_code, 3)
        self.assertIn("信息已过期", render_menu(self.home, now=later))

    def test_old_observation_can_be_confirmed_by_a_new_scan(self):
        self.call("scan", "--project", "website")
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        with sqlite3.connect(self.home / "xiaomao.sqlite") as conn:
            conn.execute("UPDATE observations SET observed_at_utc=?", (old,))
        self.call("scan", "--project", "website")
        self.call("handoff", "--project", "website")
        self.assertEqual(inspect_handoff(self.home, "website").exit_code, 0)

    def test_changed_observation_requires_new_handoff(self):
        self.report()
        (self.repo / "README.md").write_text("new state")
        self.call("scan", "--project", "website")
        view = inspect_handoff(self.home, "website")
        self.assertEqual(view.status, "交接已落后")
        self.assertNotEqual(view.exit_code, 0)

    def test_same_second_new_report_body_matches_latest_observation(self):
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with patch("xiaomao.collect.utc_now", return_value=stamp):
            self.call("scan", "--project", "website")
            (self.repo / "README.md").write_text("changed within the same second")
            self.call("scan", "--project", "website")
        path = Path(self.call("handoff", "--project", "website").strip())
        view = inspect_handoff(self.home, "website")
        self.assertEqual(view.exit_code, 0)
        self.assertIn("unstaged 1", path.read_text())
        self.assertNotIn("工作区 clean", path.read_text())

    def test_failed_latest_scan_does_not_fall_back(self):
        self.report()
        with sqlite3.connect(self.home / "xiaomao.sqlite") as conn:
            conn.execute("UPDATE scan_runs SET outcome='error'")
        view = inspect_handoff(self.home, "website")
        self.assertEqual(view.status, "采集未完成")
        self.assertNotEqual(view.exit_code, 0)

    def test_legacy_newest_report_does_not_fall_back(self):
        self.report()
        legacy = self.home / "reports/handoff/website-20990101-000000.txt"
        legacy.write_text("old unsupported format")
        view = inspect_handoff(self.home, "website")
        self.assertEqual(view.path, legacy)
        self.assertEqual(view.exit_code, 3)
        self.assertIsNone(view.body)

    def test_project_prefix_does_not_select_another_project(self):
        path = self.report()
        other = path.with_name("website-other-20990101-000000.txt")
        other.write_text("not this project")
        self.assertEqual(inspect_handoff(self.home, "website").path.resolve(), path.resolve())

    def test_hash_mismatch_and_invalid_metadata_fail_closed(self):
        path = self.report()
        path.write_text(path.read_text() + "altered")
        self.assertEqual(inspect_handoff(self.home, "website").exit_code, 3)
        path.with_suffix(".json").write_text("[]")
        self.assertEqual(inspect_handoff(self.home, "website").exit_code, 3)

    def test_future_timestamp_is_not_fresh(self):
        path = self.report()
        self.change_meta(path, lambda meta: meta.update(generated_at_utc=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        view = inspect_handoff(self.home, "website")
        self.assertEqual(view.exit_code, 3)
        self.assertIn("未来", view.reason)

    def test_scope_change_excluded_company_and_symlink_never_read_body(self):
        path = self.report()
        self.cfg.projects[0].approved_root = str(self.root / "theAIapp-service-worktree")
        save_config(self.cfg)
        with patch("xiaomao.handoff_view._read_file", side_effect=AssertionError("body read")):
            self.assertEqual(inspect_handoff(self.home, "website").status, "范围已排除")
        self.cfg.projects[0].approved_root = str(self.repo)
        save_config(self.cfg)
        path.unlink()
        path.symlink_to(self.repo / "README.md")
        self.assertEqual(inspect_handoff(self.home, "website").exit_code, 3)

    def test_disabled_worktree_scope_change_rejects_previous_report(self):
        self.report()
        self.cfg.projects[0].worktrees[0].scan = False
        save_config(self.cfg)
        self.assertEqual(inspect_handoff(self.home, "website").exit_code, 3)

    def test_reregistered_path_cannot_relabel_old_observation(self):
        self.report()
        another = init_repo(self.root / "another")
        self.cfg.projects[0].approved_root = str(another)
        self.cfg.projects[0].worktrees[0].path = str(another)
        save_config(self.cfg)
        self.call("init")
        self.call("handoff", "--project", "website", expected=3)
        view = inspect_handoff(self.home, "website")
        self.assertEqual(view.exit_code, 3)
        self.assertIn("来源", view.reason)
        self.assertNotIn("分支 / HEAD", view.path.read_text())

    def test_disabled_tree_is_not_rendered_as_scanned_before_next_init(self):
        from xiaomao.config import WorktreeSpec
        second = init_repo(self.root / "second")
        self.cfg.projects[0].worktrees.append(WorktreeSpec("second", str(second)))
        save_config(self.cfg)
        self.report()
        self.cfg.projects[0].worktrees[1].scan = False
        save_config(self.cfg)
        self.call("scan", "--project", "website")
        path = Path(self.call("handoff", "--project", "website").strip())
        section = path.read_text().split("工作树：second", 1)[1].split("测试", 1)[0]
        self.assertIn("本阶段不扫描", section)
        self.assertNotIn("分支 / HEAD", section)

    def test_reenabled_tree_cannot_borrow_other_tree_scan_time(self):
        from xiaomao.config import WorktreeSpec
        second = init_repo(self.root / "second")
        self.cfg.projects[0].worktrees.append(WorktreeSpec("second", str(second)))
        save_config(self.cfg)
        self.report()
        self.cfg.projects[0].worktrees[1].scan = False
        save_config(self.cfg)
        (second / "README.md").write_text("unobserved change")
        self.call("scan", "--project", "website")
        self.cfg.projects[0].worktrees[1].scan = True
        save_config(self.cfg)
        self.call("init")
        self.call("handoff", "--project", "website", expected=3)
        self.assertIn("覆盖当前启用范围", inspect_handoff(self.home, "website").reason)
        self.call("scan", "--project", "website")
        self.call("handoff", "--project", "website")
        self.assertEqual(inspect_handoff(self.home, "website").exit_code, 0)

    def test_company_daily_scheduled_status_and_scan_report_are_excluded(self):
        self.report()
        self.cfg.projects[0].approved_root = str(self.root / "theAIapp-service")
        save_config(self.cfg)
        with patch("xiaomao.cli._maybe_model_note", side_effect=AssertionError("model path entered")), \
             patch("xiaomao.cli.open_db", side_effect=AssertionError("DB facts entered")):
            self.call("daily", "--scheduled", expected=3)
            self.call("status", "--project", "website", expected=3)
            self.call("handoff", "--project", "website", "--with-model", expected=3)

    def test_legacy_daily_body_is_not_printed_or_opened(self):
        folder = self.home / "reports/daily"
        (folder / "2026-09-22.txt").write_text("LEGACY_UNAUTHORIZED_SENTINEL")
        out = self.call("latest", "daily", "--print", expected=3)
        self.assertNotIn("LEGACY_UNAUTHORIZED_SENTINEL", out)
        with patch("xiaomao.cli.subprocess.run", side_effect=AssertionError("open called")):
            self.call("open", "daily", expected=3)

    def test_actual_swiftbar_reader_with_spaces_in_home(self):
        self.report()
        script = Path(__file__).resolve().parents[1] / "scripts/read-handoff.py"
        payload = base64.urlsafe_b64encode(json.dumps([str(self.home), "website"]).encode()).decode()
        proc = subprocess.run([sys.executable, str(script), payload], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("资料状态：交接可读", proc.stdout)
        self.assertIn("未核实项", proc.stdout)

    def test_missing_home_query_creates_nothing(self):
        missing = self.root / "missing"
        self.assertNotEqual(inspect_handoff(missing, None).exit_code, 0)
        self.assertFalse(missing.exists())

    def test_unregistered_id_and_ambiguous_project_never_read_report(self):
        self.report()
        self.assertNotEqual(inspect_handoff(self.home, "../website").exit_code, 0)
        import copy
        other = copy.deepcopy(self.cfg.projects[0])
        other.project_id = "another"
        self.cfg.projects.append(other)
        save_config(self.cfg)
        view = inspect_handoff(self.home, None)
        self.assertNotEqual(view.exit_code, 0)
        self.assertIn("--project", view.reason)
