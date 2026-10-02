"""Annotated product-level regressions; models are always local test doubles."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.helpers import git, init_repo, website_fixture_config
from xiaomao.collect import scan_authorized
from xiaomao.config import save_config
from xiaomao.daily import build_bundle, render_bundle
from xiaomao.store import open_db


class FeatureDailyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = init_repo(self.root / "repo")
        self.cfg = website_fixture_config(self.root / "home", self.repo)
        save_config(self.cfg)
        self.conn = self.enterContext(open_db(self.root / "home/xiaomao.sqlite"))

    def scan(self, at):
        with patch("xiaomao.collect.utc_now", return_value=at):
            scan_authorized(self.conn, self.cfg)

    def bundle(self):
        return build_bundle(self.cfg, self.conn, date="2026-10-02",
                            now=datetime(2026, 10, 2, 14, tzinfo=timezone.utc))

    def catalog(self):
        from xiaomao.feature_context import save_catalog
        save_catalog(self.conn, self.cfg, "website", {
            "purpose": "汇总每天的开发进展", "features": [
                {"id": "daily", "name": "每日工作总结", "paths": ["daily*.py"],
                 "symbols": ["report"], "priority": 3},
                {"id": "recovery", "name": "任务恢复", "paths": ["recover*.py"],
                 "symbols": ["recover"], "priority": 2},
            ]}, at="2026-10-01T12:00:00+00:00")

    def test_ten_files_one_capability_and_one_commit_two_capabilities(self):
        self.catalog()
        self.scan("2026-10-01T13:00:00+00:00")
        for i in range(10):
            (self.repo / f"daily{i}.py").write_text("def report():\n    return 'all day'\n")
        (self.repo / "recover.py").write_text("def recover():\n    return 'retry'\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "Two features across eleven files")
        self.scan("2026-10-02T03:00:00+00:00")
        from xiaomao.feature_daily import feature_records
        records = feature_records(self.bundle()["projects"][0])
        self.assertEqual({r["feature_name"] for r in records}, {"每日工作总结", "任务恢复"})
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r["delivery"] == "committed" for r in records))
        body = render_bundle(self.bundle())
        self.assertIn("每日工作总结", body)
        self.assertNotIn("daily0.py", body)
        self.assertNotIn("Two features across eleven files", body)

    def test_project_background_and_code_context_are_frozen_at_observation(self):
        self.catalog()
        (self.repo / "README.md").write_text("# 每日工作总结\n汇总每天的开发进展。\n")
        (self.repo / "daily.py").write_text("def report():\n    return 'dirty only'\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "baseline")
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return 'all day'\n")
        self.scan("2026-10-02T03:00:00+00:00")
        before = self.bundle()["projects"][0]
        (self.repo / "README.md").write_text("# Tomorrow's unrelated project\n")
        (self.repo / "daily.py").write_text("def unrelated():\n    return 'tomorrow'\n")
        after = self.bundle()["projects"][0]
        self.assertEqual(before["feature_contexts"], after["feature_contexts"])
        self.assertNotIn("tomorrow", json.dumps(after))
        self.assertIn("report", json.dumps(after["activity"]))

    def test_main_report_has_four_product_sections_and_no_file_inventory(self):
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "recover.py").write_text("def recover():\n    return True\n")
        self.scan("2026-10-02T03:00:00+00:00")
        body = render_bundle(self.bundle())
        for section in ("今日总览", "各项目功能变化", "仍在推进的功能", "待确认与采集缺口"):
            self.assertIn(section, body)
        self.assertNotIn("recover.py", body)
        self.assertIn("功能影响待确认", body)

    def test_two_functions_in_one_new_file_split_by_catalog_symbols(self):
        self.catalog()
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "combined.py").write_text("def report():\n    return 'summary'\n\ndef recover():\n    return 'retry'\n")
        self.scan("2026-10-02T03:00:00+00:00")
        from xiaomao.feature_daily import feature_records
        records = feature_records(self.bundle()["projects"][0])
        self.assertEqual({r["feature_name"] for r in records}, {"每日工作总结", "任务恢复"})

    def test_yesterday_dirty_today_only_reports_the_new_delta(self):
        self.catalog()
        (self.repo / "daily.py").write_text("def report():\n    return 'yesterday'\n")
        self.scan("2026-10-01T13:00:00+00:00")
        self.scan("2026-10-02T03:00:00+00:00")
        from xiaomao.feature_daily import feature_records, project_units
        project = self.bundle()["projects"][0]
        self.assertFalse(any(r["today"] for r in feature_records(project)))
        (self.repo / "daily.py").write_text("def report():\n    return 'today'\n")
        self.scan("2026-10-02T04:00:00+00:00")
        units = [u for u in project_units(self.bundle()["projects"][0]) if not u["ongoing_only"]]
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["before"], "    return 'yesterday'")
        self.assertEqual(units[0]["after"], "    return 'today'")
        self.assertEqual(units[0]["basis"], "observation_delta")

    def test_docs_plan_and_python_formatting_do_not_become_new_capabilities(self):
        self.catalog()
        (self.repo / "daily.py").write_text("def report():\n    return 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "baseline")
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text("def report( ):\n    return (1)\n")
        (self.repo / "README.md").write_text("# 计划\n计划支持自动发布，尚未实现。\n")
        self.scan("2026-10-02T03:00:00+00:00")
        from xiaomao.feature_daily import feature_records, model_packet, validate_records
        from tests.feature_helpers import response
        project = self.bundle()["projects"][0]
        self.assertEqual({r["change_type"] for r in feature_records(project)}, {"maintenance", "design"})
        packet = model_packet(self.bundle(), project)
        for unit in packet["units"]:
            payload = {"records": [{"feature_name": "自动发布", "change_type": "addition", "before": "",
                "after": "现在支持自动发布。", "impact": "", "unit_ids": [unit["unit_id"]],
                "support": [{"unit_id": unit["unit_id"], "side": "after", "quote": unit["after"]}]}]}
            self.assertEqual(validate_records(project, packet, payload)["accepted"], 0)

    def test_model_quotes_direction_partial_failure_and_status_guards(self):
        from copy import deepcopy
        from tests.feature_helpers import response
        from xiaomao.feature_daily import model_packet, validate_records
        self.catalog()
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return 'all day'\n")
        (self.repo / "recover.py").write_text("def recover():\n    return 'retry'\n")
        self.scan("2026-10-02T03:00:00+00:00")
        project = self.bundle()["projects"][0]
        packet = model_packet(self.bundle(), project)
        daily = dict(packet, units=[u for u in packet["units"] if u["path"] == "daily.py"])
        good = response(daily, "汇总全天的工作变化。")["json"]
        result = validate_records(project, packet, good)
        self.assertEqual(result["accepted"], 1)
        self.assertGreater(result["uninterpreted_units"], 0)
        self.assertEqual({r["feature_name"] for r in result["records"]}, {"每日工作总结", "任务恢复"})
        mutations = [
            {"after": "已上线并提升性能 90%"}, {"change_type": "fix"},
            {"feature_name": "无关支付功能"}, {"unit_ids": ["yesterday-or-forged"]},
            {"support": [{"unit_id": daily["units"][0]["unit_id"], "side": "before", "quote": "invented"}]},
        ]
        for change in mutations:
            bad = deepcopy(good)
            bad["records"][0].update(change)
            with self.subTest(change=change):
                self.assertEqual(validate_records(project, packet, bad)["accepted"], 0)

    def test_conflicting_worktrees_remain_separate(self):
        self.cfg.projects[0].worktree_policy = "all"
        self.catalog()
        linked = self.root / "linked"
        git(self.repo, "worktree", "add", "--detach", str(linked))
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return 'version one'\n")
        (linked / "daily.py").write_text("def report():\n    return 'version two'\n")
        self.scan("2026-10-02T03:00:00+00:00")
        from xiaomao.feature_daily import feature_records
        records = feature_records(self.bundle()["projects"][0])
        self.assertEqual(len(records), 2)
        self.assertEqual(len({r["variant"] for r in records}), 2)
        self.assertTrue(all(r["feature_name"] == "每日工作总结" for r in records))

    def test_committed_then_reverted_has_no_net_new_capability(self):
        self.catalog()
        (self.repo / "daily.py").write_text("def report():\n    return 'original'\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "baseline")
        self.scan("2026-10-01T13:00:00+00:00")
        for value in ("attempt", "original"):
            (self.repo / "daily.py").write_text(f"def report():\n    return '{value}'\n")
            git(self.repo, "add", ".")
            git(self.repo, "commit", "-m", value)
        self.scan("2026-10-02T03:00:00+00:00")
        from xiaomao.feature_daily import feature_records
        records = feature_records(self.bundle()["projects"][0])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["progress"], "restored")
        self.assertEqual(records[0]["delivery"], "committed")

    def test_future_catalog_correction_does_not_rewrite_history(self):
        self.catalog()
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return 'all day'\n")
        self.scan("2026-10-02T03:00:00+00:00")
        before = self.bundle()
        from xiaomao.feature_context import save_catalog
        save_catalog(self.conn, self.cfg, "website", {"purpose": "次日修正", "features": [
            {"id": "other", "name": "次日名称", "paths": ["daily.py"]}]}, at="2026-10-03T03:00:00+00:00")
        self.scan("2026-10-03T04:00:00+00:00")
        after = self.bundle()
        self.assertEqual(before["evidence_hash"], after["evidence_hash"])

    def test_partial_workspace_restore_does_not_claim_new_functionality(self):
        self.catalog()
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return 'temporary'\n")
        (self.repo / "recover.py").write_text("def recover():\n    return 'pending'\n")
        self.scan("2026-10-02T03:00:00+00:00")
        (self.repo / "daily.py").unlink()
        self.scan("2026-10-02T04:00:00+00:00")
        from xiaomao.feature_daily import feature_records
        records = feature_records(self.bundle()["projects"][0])
        daily = [r for r in records if r["feature_name"] == "每日工作总结"]
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["progress"], "restored")

    def test_superseded_version_cannot_support_final_behavior(self):
        from xiaomao.feature_daily import model_packet, validate_records
        from tests.feature_helpers import response
        self.catalog()
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return 'first'\n")
        self.scan("2026-10-02T03:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return 'second'\n")
        self.scan("2026-10-02T04:00:00+00:00")
        project = self.bundle()["projects"][0]
        packet = model_packet(self.bundle(), project)
        old = [u for u in packet["units"] if u["superseded"]]
        self.assertTrue(old)
        bad = response(dict(packet, units=old))["json"]
        self.assertEqual(validate_records(project, packet, bad)["accepted"], 0)

    def test_return_to_yesterdays_dirty_content_is_a_restored_attempt(self):
        self.catalog()
        original = "def report():\n    return 'yesterday draft'\n"
        (self.repo / "daily.py").write_text(original)
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return 'today attempt'\n")
        self.scan("2026-10-02T03:00:00+00:00")
        (self.repo / "daily.py").write_text(original)
        self.scan("2026-10-02T04:00:00+00:00")
        from xiaomao.feature_daily import feature_records
        records = feature_records(self.bundle()["projects"][0])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["progress"], "restored")
        self.assertTrue(records[0]["has_ongoing"])

    def test_same_second_observations_keep_version_order(self):
        self.catalog()
        self.scan("2026-10-01T13:00:00+00:00")
        for value in ("first", "second"):
            (self.repo / "daily.py").write_text(f"def report():\n    return '{value}'\n")
            self.scan("2026-10-02T03:00:00+00:00")
        from xiaomao.feature_daily import project_units
        units = project_units(self.bundle()["projects"][0])
        first = [u for u in units if "first" in u["after"]]
        self.assertTrue(first)
        self.assertTrue(all(u["superseded"] for u in first))

    def test_changed_staging_area_does_not_masquerade_as_content_restore(self):
        self.catalog()
        original = "def report():\n    return 'draft'\n"
        (self.repo / "daily.py").write_text(original)
        self.scan("2026-10-01T13:00:00+00:00")
        git(self.repo, "add", "daily.py")
        self.scan("2026-10-02T03:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return 'staged alternative'\n")
        git(self.repo, "add", "daily.py")
        (self.repo / "daily.py").write_text(original)
        self.scan("2026-10-02T04:00:00+00:00")
        from xiaomao.feature_daily import project_units
        units = project_units(self.bundle()["projects"][0])
        self.assertFalse(any(u["net_restored"] for u in units))

    def test_context_redaction_and_budget_do_not_expose_secrets(self):
        (self.repo / "README.md").write_text('purpose\npassword = "never-persist-context"\n'
            'private_key = "never-persist-private"\nclientSecret = "never-persist-client"\n')
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text('def report():\n    password = "never-persist-code"\n    return 1\n')
        self.scan("2026-10-02T03:00:00+00:00")
        self.assertNotIn("never-persist", "\n".join(self.conn.iterdump()))
        with patch("xiaomao.ops.storage_over_budget", return_value=True):
            (self.repo / "daily.py").write_text("def report():\n    return 'budgeted-away'\n")
            self.scan("2026-10-02T04:00:00+00:00")
        self.assertNotIn("budgeted-away", "\n".join(self.conn.iterdump()))

    def test_briefing_cache_is_reused_as_static_background(self):
        from xiaomao.briefing import write_cache, load_source, empty_understanding
        docs = Path(self.cfg.briefing_docs_root) / "website"
        docs.mkdir(parents=True)
        (docs / "00-项目说明.md").write_text("# 小猫\n用途：每日开发总结\n")
        source, _ = load_source(self.cfg, "website")
        understanding = empty_understanding()
        understanding["purpose"] = "每日开发总结"
        with patch("xiaomao.briefing.utc_now", return_value="2026-10-01T12:00:00+00:00"):
            write_cache(self.cfg, source, understanding, model="fake", chunk_count=1)
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return 1\n")
        self.scan("2026-10-02T03:00:00+00:00")
        contexts = self.bundle()["projects"][0]["feature_contexts"].values()
        self.assertTrue(any(c["purpose"] == "每日开发总结" for c in contexts))
        self.assertTrue(all(c["usage"] == "static_background_only" for c in contexts))

    def test_small_behavior_change_ranks_above_large_maintenance(self):
        self.catalog()
        (self.repo / "daily.py").write_text("def report():\n    return False\n")
        for i in range(12):
            (self.repo / f"recover{i}.py").write_text("def recover():\n    return 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "baseline")
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return True\n")
        for i in range(12):
            (self.repo / f"recover{i}.py").write_text("def recover( ):\n    return (1)\n")
        self.scan("2026-10-02T03:00:00+00:00")
        from xiaomao.feature_daily import feature_records
        rows = feature_records(self.bundle()["projects"][0])
        self.assertEqual(rows[0]["feature_name"], "每日工作总结")
        self.assertEqual(rows[1]["change_type"], "maintenance")

    def test_catalog_command_read_only_show_and_append_only_corrections(self):
        import io
        from contextlib import redirect_stdout
        from xiaomao.cli import main
        self.catalog()
        self.conn.commit()
        output = io.StringIO()
        before = self.conn.total_changes
        with redirect_stdout(output), patch("xiaomao.ollama_runtime.OllamaClient", side_effect=AssertionError("model")):
            self.assertEqual(main(["--home", self.cfg.home, "features", "show", "--project", "website"]), 0)
        self.assertIn("每日工作总结", output.getvalue())
        self.assertEqual(before, self.conn.total_changes)
        source = self.root / "correction.json"
        source.write_text(json.dumps({"purpose": "修正描述", "features": [
            {"id": "daily", "name": "开发日报", "paths": ["daily*.py"]}]}))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--home", self.cfg.home, "features", "set", "--project", "website", "--file", str(source)]), 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM feature_catalogs").fetchone()[0], 2)

    def test_old_evidence_without_context_degrades_and_never_reads_live_code(self):
        self.scan("2026-10-01T13:00:00+00:00")
        (self.repo / "daily.py").write_text("def report():\n    return True\n")
        self.scan("2026-10-02T03:00:00+00:00")
        bundle = self.bundle()
        project = bundle["projects"][0]
        project["feature_contexts"] = {}
        for event in project["activity"] + project["ongoing"]:
            for file in event["files"]:
                file.pop("units", None)
                file.pop("snapshot", None)
        with patch("xiaomao.change_evidence.file_text", side_effect=AssertionError("live read")):
            body = render_bundle(bundle)
        self.assertIn("缺少当时的项目背景", body)
        self.assertIn("功能影响待确认", body)

    def test_context_cache_directory_cannot_escape_home(self):
        from xiaomao.briefing import write_cache, load_source, empty_understanding
        from xiaomao.feature_context import _briefing
        docs = Path(self.cfg.briefing_docs_root) / "website"
        docs.mkdir(parents=True)
        (docs / "00-项目说明.md").write_text("# 项目\n用途：测试背景\n")
        source, _ = load_source(self.cfg, "website")
        understanding = empty_understanding()
        understanding["purpose"] = "越出小猫目录的缓存"
        with patch("xiaomao.briefing.utc_now", return_value="2026-10-01T12:00:00+00:00"):
            cache = write_cache(self.cfg, source, understanding, model="fake", chunk_count=1)
        outside = self.root / "other-cache"
        cache.parent.rename(outside)
        cache.parent.symlink_to(outside, target_is_directory=True)
        self.assertIsNone(_briefing(self.cfg, "website", "2026-10-02T03:00:00+00:00"))

    def test_legacy_unbound_model_note_cannot_bypass_functional_validation(self):
        from xiaomao.reports import render_daily, write_daily
        self.scan("2026-10-01T13:00:00+00:00")
        body = render_daily(self.cfg, self.conn, date="2026-10-02", model_note="功能已经上线")
        self.assertNotIn("功能已经上线", body)
        path = write_daily(self.cfg, self.conn, date="2026-10-02", model_note="功能已经上线", model_ok=True)
        self.assertNotIn("功能已经上线", path.read_text())


if __name__ == "__main__":
    unittest.main()
