from __future__ import annotations

import io
import json
import os
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import chdir, redirect_stdout
from itertools import product
from pathlib import Path
from unittest.mock import Mock, patch

from xiaomao import schedule
from xiaomao.cli import main
from xiaomao.config import default_config, save_config
from xiaomao.eval_runner import host_pressure, run_eval, self_check_validators
from xiaomao.eval_samples import samples
from xiaomao.lock import ScanLock
from xiaomao.migrate import backup_sqlite, migrate_models_dir
from xiaomao.schedule import START_INTERVAL_SECONDS, plist_payload
from xiaomao.store import open_db
from xiaomao.summarize import degrade_note, summarize_or_degrade, validate_model_json
from tests.helpers import init_repo, git, website_fixture_config


class ReportCliTests(unittest.TestCase):
    def _home(self) -> Path:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = init_repo(root / "repo")
        home = root / "home"
        cfg = website_fixture_config(home, repo, extra_root=root)
        home.mkdir(parents=True)
        save_config(cfg)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--home", str(home), "init"]), 0)
            self.assertEqual(main(["--home", str(home), "scan", "--project", "website"]), 0)
        return home

    def test_daily_and_handoff_are_rule_based(self) -> None:
        home = self._home()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--home", str(home), "daily", "--date", "2026-09-20"]), 0)
            self.assertEqual(main(["--home", str(home), "handoff", "--project", "website"]), 0)
        out = buf.getvalue()
        self.assertIn("reports/daily/2026-09-20.txt", out.replace("\\", "/"))
        body = (home / "reports" / "daily" / "2026-09-20.txt").read_text(encoding="utf-8")
        # The body is the plain evening report: no technical wording, gaps counted in one line.
        self.assertTrue(body.startswith("小猫晚报 · 9月20日（周日）\n"))
        # A missing scan baseline is not a failure: no issue line in the body.
        self.assertNotIn("采集问题", body)
        self.assertNotIn("未核实", body)
        self.assertNotIn("partial", body)
        # The evidence keeps the rigorous wording unchanged.
        daily = (home / "reports" / "daily-evidence" / "2026-09-20.txt").read_text(encoding="utf-8")
        self.assertIn("本报告由规则程序生成", daily)
        self.assertIn("测试：unknown", daily)
        self.assertIn("该窗口没有扫描覆盖证据", daily)
        self.assertIn("覆盖：partial", daily)
        self.assertNotIn("用户今天没有工作", daily)
        self.assertNotIn("测试通过", daily)
        self.assertIn("已核实", daily)
        self.assertIn("未核实", daily)
        handoffs = list((home / "reports" / "handoff").glob("website-*.txt"))
        self.assertTrue(handoffs)
        text = handoffs[0].read_text(encoding="utf-8")
        self.assertIn("unknown", text)
        self.assertIn("约束", text)

    def test_handoff_checks_completion_per_sentence(self) -> None:
        from xiaomao.config import load_config
        from xiaomao.ops import record_summary_row
        from xiaomao.reports import facts_payload

        cases = (
            ("interpretations", "测试结果未知。笔记已上线。", False),
            ("interpretations", "测试结果未知，但笔记已上线。", False),
            ("interpretations", "没有证据表明测试通过但笔记已上线。", False),
            ("interpretations", "没有证据表明笔记已上线。", True),
            ("unknowns", "测试结果未知。笔记已上线。", False),
            ("unknowns", "没有证据表明笔记已上线。", True),
        )
        for (section, model_text, accepted), cached in product(cases, (False, True)):
            with self.subTest(section=section, model_text=model_text, cached=cached):
                home = self._home()
                payload = {"interpretations": [], "suggestions": [], "unknowns": []}
                item = {"text": model_text}
                if section == "interpretations":
                    item["evidence_ids"] = ["worktree:website-main"]
                payload[section].append(item)
                if cached:
                    with open_db(home / "xiaomao.sqlite") as conn:
                        facts = facts_payload(load_config(home), conn, "website")
                        record_summary_row(conn, facts=facts, result={"ok": True, "payload": payload}, model="fixture")
                client = Mock()
                client.generate_json.return_value = {"json": payload}
                with (
                    patch("xiaomao.ollama_runtime.models_dir_allowed"),
                    patch("xiaomao.ollama_runtime.inspect_running", return_value={"reachable": True}),
                    patch("xiaomao.ollama_runtime.OllamaClient", return_value=client),
                    patch("xiaomao.ollama_runtime.api_get", return_value={}),
                    patch("xiaomao.eval_runner.host_pressure", return_value={}),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(main(["--home", str(home), "handoff", "--project", "website", "--with-model"]), 0)
                self.assertEqual(client.generate_json.call_count, 0 if cached else 1)
                path = next((home / "reports" / "handoff").glob("website-*.txt"))
                body = path.read_text(encoding="utf-8")
                self.assertEqual("校验：pass" in body, accepted)
                if accepted:
                    self.assertIn(model_text, body)
                else:
                    self.assertIn("本报告由规则程序生成", body)
                    self.assertNotIn("笔记已上线", body)

    def test_handoff_reuse_preserves_model_unknowns(self) -> None:
        home = self._home()
        limit = "离线导出能否恢复原有笔记，还没有验证"
        client = Mock()
        client.generate_json.return_value = {
            "json": {"interpretations": [], "suggestions": [], "unknowns": [{"text": limit}]},
        }
        args = ["--home", str(home), "handoff", "--project", "website", "--with-model"]
        bodies = []
        with (
            patch("xiaomao.ollama_runtime.models_dir_allowed"),
            patch("xiaomao.ollama_runtime.inspect_running", return_value={"reachable": True}),
            patch("xiaomao.ollama_runtime.OllamaClient", return_value=client),
            patch("xiaomao.ollama_runtime.api_get", return_value={}),
            patch("xiaomao.eval_runner.host_pressure", return_value={}),
        ):
            for _ in range(2):
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(args), 0)
                bodies.append(Path(output.getvalue().strip()).read_text(encoding="utf-8"))
        self.assertEqual(client.generate_json.call_count, 1)
        self.assertTrue(all("校验：pass" in body for body in bodies))
        self.assertEqual([limit in body for body in bodies], [True, True], limit)

    def test_handoff_reuse_marks_legacy_summary_without_unknowns(self) -> None:
        from xiaomao.ops import evidence_snapshot_key, record_summary_row, reuse_summary_note
        from xiaomao.store import latest_summary_for_range

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / "xiaomao.sqlite"
        facts = {"project_id": "website"}
        text = "可继续检查离线导出"
        with open_db(db) as conn:
            record_summary_row(
                conn, facts=facts, model="fixture",
                result={"ok": True, "payload": {"interpretations": [{"text": text}]}},
            )
            if any(column[1] == "unknowns_json" for column in conn.execute("PRAGMA table_info(summaries)")):
                conn.execute("ALTER TABLE summaries DROP COLUMN unknowns_json")
            conn.execute("UPDATE meta SET value='5' WHERE key='schema'")

        with open_db(db) as conn:
            row = latest_summary_for_range(conn, "website", evidence_snapshot_key(facts))
            note = reuse_summary_note(row, facts)
        self.assertIn(text, note)
        self.assertIn("这份旧解读没有保存“未知”一栏", note)

    def test_facts_payload_includes_worktree_evidence_ids(self) -> None:
        from xiaomao.config import load_config
        from xiaomao.reports import facts_payload
        from xiaomao.store import open_db

        home = self._home()
        cfg = load_config(home)
        with open_db(home / "xiaomao.sqlite") as conn:
            payload = facts_payload(cfg, conn, "website")
        ids = payload["evidence_ids"]
        self.assertIn("worktree:website-main", ids)
        self.assertTrue(any(i.startswith("worktree:") for i in ids))
        self.assertEqual(payload["test_status"], "unknown")
        self.assertEqual(payload["deploy_status"], "unknown")
        self.assertTrue(any(wt.get("evidence_id") == "worktree:website-main" for wt in payload["worktrees"]))
        self.assertIn("last_commit_at", payload["worktrees"][0])
        self.assertIn("module_digest", payload["worktrees"][0])

    def test_question_about_shipped_still_rejected(self) -> None:
        facts = {"evidence_ids": ["ev_1"], "test_status": "unknown", "deploy_status": "unknown"}
        payload = {
            "interpretations": [{"text": "工作区 clean", "evidence_ids": ["ev_1"]}],
            "suggestions": [{"text": "确认是否已上线", "evidence_ids": ["ev_1"]}],
            "unknowns": [],
        }
        errors = validate_model_json(payload, facts)
        self.assertTrue(any(e.startswith("unwarranted_completion") for e in errors))

    def test_default_depth_candidate_is_coder_30b(self) -> None:
        cfg = default_config(Path(self.enterContext(tempfile.TemporaryDirectory())))
        self.assertEqual(cfg.depth_candidates[0], "qwen3-coder:30b")
        self.assertIn("qwen3.6:35b", cfg.depth_candidates)

    def test_eval_dry_run_degrades(self) -> None:
        home = self._home()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--home", str(home), "eval", "--dry-run"]), 0)
        payload = json.loads(buf.getvalue())
        self.assertGreaterEqual(payload["n"], 20)
        self.assertEqual(payload["ok"], 0)
        self.assertEqual(payload["degraded"], payload["n"])

    def test_pilot_archive_keeps_round_and_restart_starts_fresh(self) -> None:
        home = self._home()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--home", str(home), "pilot", "start"]), 0)
        with open_db(home / "xiaomao.sqlite") as conn:
            first_start = conn.execute("SELECT value FROM meta WHERE key='pilot_started_at'").fetchone()["value"]
            conn.execute("UPDATE meta SET value='2026-01-01T00:00:00+00:00' WHERE key='pilot_started_at'")
        # A reason is required; without it nothing changes.
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--home", str(home), "pilot", "archive"]), 2)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--home", str(home), "pilot", "archive", "--reason", "观察范围不足"]), 0)
        archived = json.loads(buf.getvalue())
        self.assertEqual(archived["status"], "PILOT_ARCHIVED")
        self.assertEqual(archived["started_at"], "2026-01-01T00:00:00+00:00")
        self.assertGreaterEqual(archived["stats"]["scan_outcomes"].get("success", 0), 1)
        # Archiving twice is refused: the round is already closed.
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--home", str(home), "pilot", "archive", "--reason", "x"]), 2)
        with open_db(home / "xiaomao.sqlite") as conn:
            events = conn.execute("SELECT payload_json FROM events WHERE kind='pilot_archived'").fetchall()
            self.assertEqual(len(events), 1)
            self.assertEqual(json.loads(events[0]["payload_json"])["reason"], "观察范围不足")
            scans = conn.execute("SELECT COUNT(*) AS n FROM scan_runs").fetchone()["n"]
        self.assertGreaterEqual(scans, 1)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--home", str(home), "pilot", "start"]), 0)
        restarted = json.loads(buf.getvalue())
        self.assertEqual(restarted["status"], "PILOT_RUNNING")
        self.assertNotEqual(restarted["started_at"], "2026-01-01T00:00:00+00:00")
        self.assertGreaterEqual(restarted["started_at"], first_start)
        with open_db(home / "xiaomao.sqlite") as conn:
            self.assertIsNone(conn.execute("SELECT value FROM meta WHERE key='pilot_archived_at'").fetchone())
            # History is kept, not reset.
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM scan_runs").fetchone()["n"], scans)


class SummarizeTests(unittest.TestCase):
    def test_validator_rejects_unwarranted_pass(self) -> None:
        problems = self_check_validators()
        self.assertEqual(problems, [])

    def test_valid_payload_accepted(self) -> None:
        facts = {"evidence_ids": ["ev_1"], "test_status": "unknown", "deploy_status": "unknown"}
        payload = {
            "interpretations": [{"text": "工作区 clean，不能据此判断已上线与否", "evidence_ids": ["ev_1"]}],
            "suggestions": [{"text": "需要授权的测试报告才能更新测试栏", "evidence_ids": []}],
            "unknowns": [{"text": "测试与部署仍 unknown"}],
        }
        self.assertEqual(validate_model_json(payload, facts), [])

    def test_assertive_completion_still_rejected(self) -> None:
        facts = {"evidence_ids": [], "test_status": "unknown", "deploy_status": "unknown"}
        payload = {
            "interpretations": [{"text": "测试通过，网站已上线", "evidence_ids": []}],
            "suggestions": [{"text": "无", "evidence_ids": []}],
            "unknowns": [],
        }
        errors = validate_model_json(payload, facts)
        self.assertTrue(any(e.startswith("unwarranted_completion") for e in errors))

    def test_degrade_without_client(self) -> None:
        from xiaomao.config import default_config

        cfg = default_config(Path(self.enterContext(tempfile.TemporaryDirectory())))
        result = summarize_or_degrade(cfg, {"evidence_ids": []}, client=None)
        self.assertTrue(result["degraded"])
        self.assertIn("规则程序", result["model_note"])
        self.assertTrue(degrade_note("x"))

    def test_host_pressure_keys(self) -> None:
        snap = host_pressure()
        self.assertIn("memory_bytes", snap)
        self.assertIn("swap_used", snap)

    def test_sample_count(self) -> None:
        self.assertGreaterEqual(len(samples()), 20)
        cats = {s.category for s in samples()}
        for needed in (
            "chinese_summary",
            "git_diff",
            "json",
            "old_test_evidence",
            "unknown",
            "truncated",
            "secret_bait",
            "prompt_injection",
        ):
            self.assertIn(needed, cats)

    def test_eval_usable_count_requires_nonblank_model_content(self) -> None:
        cfg = default_config(Path(self.enterContext(tempfile.TemporaryDirectory())))
        sample = next(sample for sample in samples() if sample.sample_id == "zh-summary-dirty")
        cases = (
            ("empty", {}, 0),
            ("whitespace only", {"interpretations": [{"text": " \n\t", "evidence_ids": ["ev_unstaged_1"]}]}, 0),
            ("evidence-backed answer", {"interpretations": [{
                "text": "两个文件有未暂存修改，另有一份未跟踪的笔记。", "evidence_ids": ["ev_unstaged_1"],
            }]}, 1),
            ("states what is missing", {"unknowns": [{"text": "记录只列出文件名，缺少具体改动内容。"}]}, 1),
        )
        client = Mock()
        with patch("xiaomao.eval_runner.host_pressure", return_value={}):
            for label, content, expected in cases:
                with self.subTest(label=label):
                    client.generate_json.return_value = {"json": {
                        "interpretations": [], "suggestions": [], "unknowns": [], **content,
                    }}
                    report = run_eval(cfg, client=client, model=cfg.ask_model, selected=[sample])
                    self.assertEqual(report["ok"], 1)
                    self.assertEqual(report["usable"], expected)

    def test_eval_counts_sample_specific_forbidden_claims(self) -> None:
        cfg = default_config(Path(self.enterContext(tempfile.TemporaryDirectory())))
        selected = {sample.sample_id: sample for sample in samples()}
        cases = (
            ("truncated-diff", "完整实现了 HandleSession", [], (1, 0)),
            ("truncated-diff", "完整实现了 HandleSession", [{"text": "测试未知"}], (1, 0)),
            ("truncated-diff", "不能说完整实现了 HandleSession", [], (0, 1)),
            ("truncated-diff", "完整实现了 HandleSession，但测试未知。", [], (1, 0)),
            ("truncated-diff", "尚无证据表明完整实现了 HandleSession。", [], (0, 1)),
            ("truncated-diff", "完整实现了 HandleSession 这一说法缺乏证据。", [], (0, 1)),
            ("truncated-diff", "不能说完整实现了 HandleSession但后来确认完整实现了 HandleSession。", [], (1, 0)),
            ("git-diff-conflict-markers-absent", "冲突未解决", [], (1, 0)),
        )
        client = Mock()
        for sample_id, text, unknowns, expected in cases:
            with self.subTest(sample_id=sample_id, text=text, unknowns=unknowns):
                sample = selected[sample_id]
                client.generate_json.return_value = {"json": {
                    "interpretations": [{"text": text, "evidence_ids": sample.facts["evidence_ids"]}],
                    "suggestions": [],
                    "unknowns": unknowns,
                }}
                with patch("xiaomao.eval_runner.host_pressure", return_value={}):
                    report = run_eval(cfg, client=client, model=cfg.ask_model, selected=[sample])
                self.assertEqual((report["serious_factual_errors"], report["usable"]), expected)
                self.assertEqual(report["samples"][0]["forbidden_hits"], list(sample.must_not) if expected[0] else [])

    def test_extract_json_object_from_think_and_fences(self) -> None:
        from xiaomao.ollama_runtime import extract_json_object

        obj = {
            "interpretations": [{"text": "clean", "evidence_ids": ["ev_1"]}],
            "suggestions": [],
            "unknowns": [{"text": "测试 unknown"}],
        }
        blob = json.dumps(obj, ensure_ascii=False)
        self.assertEqual(extract_json_object(blob), obj)
        self.assertEqual(extract_json_object(f"```json\n{blob}\n```"), obj)
        wrapped = f"<think>先核对证据，不要发明测试通过。</think>\n{blob}"
        self.assertEqual(extract_json_object(wrapped), obj)
        self.assertIsNone(extract_json_object(""))
        self.assertIsNone(extract_json_object("not json at all"))
        # Extraction must not invent a payload; leftover prose stays invalid.
        self.assertIsNone(extract_json_object("测试通过，已上线"))

    def test_chat_json_payload_disables_think(self) -> None:
        from xiaomao.config import default_config
        from xiaomao.ollama_runtime import chat_json_payload
        from xiaomao.summarize import JSON_SCHEMA

        cfg = default_config(Path(self.enterContext(tempfile.TemporaryDirectory())))
        payload = chat_json_payload(
            cfg,
            model="qwen3.6:35b",
            system="sys",
            user="user",
            schema=JSON_SCHEMA,
        )
        self.assertIs(payload["think"], False)
        self.assertNotIn("think", payload["options"])
        self.assertEqual(payload["options"]["num_ctx"], cfg.context_length)
        self.assertEqual(payload["options"]["num_predict"], 2048)
        self.assertEqual(payload["keep_alive"], 0)
        self.assertEqual(payload["format"], JSON_SCHEMA)


class MigrateLockScheduleTests(unittest.TestCase):
    def test_backup_sqlite_is_consistent(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        src = root / "src.sqlite"
        dest = root / "dest.sqlite"
        with open_db(src) as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                ("probe", "1"),
            )
        report = backup_sqlite(src, dest)
        self.assertEqual(report["integrity"], "ok")
        con = sqlite3.connect(dest)
        self.assertEqual(con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], "6")
        self.assertEqual(con.execute("SELECT value FROM meta WHERE key='probe'").fetchone()[0], "1")
        con.close()
        with self.assertRaises(FileExistsError):
            backup_sqlite(src, dest)

    def test_migrate_models_dir_keeps_source(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        src = root / "legacy"
        dest = root / "xiaomao-ollama"
        (src / "manifests" / "library" / "gemma4").mkdir(parents=True)
        (src / "blobs").mkdir()
        (src / "manifests" / "library" / "gemma4" / "12b").write_text("manifest", encoding="utf-8")
        (src / "blobs" / "sha256-abc").write_bytes(b"blob-bytes")
        report = migrate_models_dir(src, dest)
        self.assertTrue(report["src_retained"])
        self.assertEqual((src / "blobs" / "sha256-abc").read_bytes(), b"blob-bytes")
        self.assertEqual((dest / "blobs" / "sha256-abc").read_bytes(), b"blob-bytes")
        self.assertEqual(report["after"]["blob_count"], 1)
        self.assertEqual(report["after"]["manifest_count"], 1)
        (dest / "extra").write_text("nope", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            migrate_models_dir(src, dest)

    def test_scan_lock_exclusive(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        lock = root / "xiaomao.lock"
        with ScanLock(lock):
            with self.assertRaises(RuntimeError):
                with ScanLock(lock):
                    pass

    def test_plist_interval_is_five_minutes(self) -> None:
        self.assertEqual(START_INTERVAL_SECONDS, 300)
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        payload = plist_payload(home=home, log_dir=home / "logs")
        self.assertEqual(payload["StartInterval"], 300)
        self.assertEqual(payload["Label"], "ai.xiaomao.scan")
        joined = " ".join(payload["ProgramArguments"])
        self.assertIn("xiaomao-scan.sh", joined)
        self.assertNotIn("xiaomao-daily.sh", joined)
        self.assertNotIn("with-model", joined)
        script = Path(__file__).resolve().parents[1] / "scripts" / "xiaomao-scan.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn(" scan", text)
        self.assertNotIn("with-model", text)
        self.assertNotIn("daily", text)
        exec_line = [ln for ln in text.splitlines() if ln.startswith("exec ")][-1]
        self.assertIn(" scan", exec_line)
        self.assertNotIn("--project", exec_line)
        self.assertNotIn("eval", exec_line)

    def test_daily_plist_is_2130_and_separate_from_scan(self) -> None:
        from xiaomao.schedule import DAILY_LABEL, daily_plist_payload

        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        payload = daily_plist_payload(home=home, log_dir=home / "logs")
        self.assertEqual(payload["Label"], DAILY_LABEL)
        self.assertEqual(payload["StartCalendarInterval"], {"Hour": 21, "Minute": 30})
        self.assertEqual(payload["StartInterval"], 300)
        self.assertTrue(payload["RunAtLoad"])
        joined = " ".join(payload["ProgramArguments"])
        self.assertIn("xiaomao-daily.sh", joined)
        self.assertNotIn("xiaomao-scan.sh", joined)
        script = Path(__file__).resolve().parents[1] / "scripts" / "xiaomao-daily.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("daily --scheduled", text)
        self.assertNotIn("with-model", text)

    def test_schedule_install_persists_selected_python(self) -> None:
        home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (home / "config.json").write_text('{"projects": []}', encoding="utf-8")
        chosen_python = home / "custom bin" / "python3"
        chosen_python.parent.mkdir()
        chosen_python.symlink_to(sys.executable)
        installer_python = str(home / "installer bin" / "python3.11")
        cases = (
            ("default", {}, installer_python),
            ("absolute", {"XIAOMAO_PYTHON": str(chosen_python)}, str(chosen_python)),
            ("relative", {"XIAOMAO_PYTHON": "./custom bin/python3"}, str(chosen_python)),
            ("command", {"XIAOMAO_PYTHON": "python3", "PATH": "custom bin"}, str(chosen_python)),
        )
        loaded = subprocess.CompletedProcess([], 0, "", "")
        with (
            chdir(home),
            patch.object(schedule.sys, "executable", installer_python),
            patch.object(schedule, "bootout"),
            patch.object(schedule, "bootstrap", return_value=loaded),
        ):
            for choice, env, expected in cases:
                with patch.dict(os.environ, env, clear=True):
                    for install_job in (schedule.install, schedule.install_daily):
                        with self.subTest(job=install_job.__name__, choice=choice):
                            path = home / f"{install_job.__name__}.plist"
                            install_job(home=home, log_dir=home / "logs", path=path)
                            payload = plistlib.loads(path.read_bytes())
                            self.assertEqual(payload["EnvironmentVariables"].get("XIAOMAO_PYTHON"), expected)

    def test_schedule_scripts_use_path_python_without_configured_python(self) -> None:
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (home / "config.json").write_text('{"projects": []}', encoding="utf-8")
        python = home / "python3"
        python.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "-c" ]; then exec "$TEST_PYTHON" "$@"; fi\n'
            'printf "%s\\n" "$@"\n',
            encoding="utf-8",
        )
        python.chmod(0o700)
        env = {"PATH": str(home), "XIAOMAO_HOME": str(home), "TEST_PYTHON": sys.executable,
               "PYTHONDONTWRITEBYTECODE": "1"}
        jobs = (
            (schedule.scan_program_argv, ["scan"]),
            (schedule.daily_program_argv, ["daily", "--scheduled"]),
        )
        for program_argv, command in jobs:
            with self.subTest(job=command[0]):
                proc = subprocess.run(program_argv(home=home), cwd=home, env=env,
                                      capture_output=True, text=True, timeout=10)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.splitlines(), ["-m", "xiaomao", "--home", str(home), *command])

    def test_scan_runs_recorded_and_pause_skips(self) -> None:
        home = Path(self.enterContext(tempfile.TemporaryDirectory())) / "h"
        root = home.parent
        repo = init_repo(root / "repo")
        cfg = website_fixture_config(home, repo, extra_root=root)
        home.mkdir()
        save_config(cfg)
        self.assertEqual(main(["--home", str(home), "init"]), 0)
        self.assertEqual(main(["--home", str(home), "scan", "--project", "website"]), 0)
        with open_db(home / "xiaomao.sqlite") as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM scan_runs WHERE outcome='success'").fetchone()["n"]
            self.assertGreaterEqual(n, 1)
        self.assertEqual(main(["--home", str(home), "pause", "scan"]), 0)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--home", str(home), "scan", "--project", "website"]), 3)
        self.assertIn("paused", buf.getvalue())
        with open_db(home / "xiaomao.sqlite") as conn:
            skipped = conn.execute("SELECT COUNT(*) AS n FROM scan_runs WHERE outcome='skip'").fetchone()["n"]
            self.assertGreaterEqual(skipped, 1)
        self.assertEqual(main(["--home", str(home), "resume", "scan"]), 0)

    def test_should_call_depth_model_table(self) -> None:
        from xiaomao.ops import pause_infer, should_call_depth_model

        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with open_db(home / "db.sqlite") as conn:
            facts = {
                "project_id": "website",
                "worktrees": [
                    {
                        "worktree_id": "website-main",
                        "observation_id": "obs_1",
                        "head_oid": "abc",
                        "staged_count": 0,
                        "unstaged_count": 0,
                        "untracked_count": 0,
                        "scan_enabled": True,
                    }
                ],
            }
            call, reason = should_call_depth_model(conn, facts, with_model=False, scheduled=False)
            self.assertFalse(call)
            self.assertEqual(reason, "rules_only")
            call, reason = should_call_depth_model(conn, facts, with_model=False, scheduled=True)
            self.assertFalse(call)
            self.assertEqual(reason, "no_new_evidence")
            call, reason = should_call_depth_model(conn, facts, with_model=True, scheduled=False)
            self.assertTrue(call)
            pause_infer(conn)
            call, reason = should_call_depth_model(conn, facts, with_model=True, scheduled=False)
            self.assertFalse(call)
            self.assertEqual(reason, "infer_paused")

    def test_retry_once_then_degrade(self) -> None:
        from xiaomao.config import default_config
        from xiaomao.summarize import summarize_or_degrade

        class Boom:
            def __init__(self) -> None:
                self.n = 0
                self.stopped = []

            def generate_json(self, **kwargs):
                self.n += 1
                raise RuntimeError("busy")

            def stop(self, model: str) -> None:
                self.stopped.append(model)

        cfg = default_config(Path(self.enterContext(tempfile.TemporaryDirectory())))
        client = Boom()
        result = summarize_or_degrade(cfg, {"evidence_ids": []}, client=client)
        self.assertTrue(result["degraded"])
        self.assertEqual(client.n, 2)
        self.assertEqual(result["retry_count"], 1)
        self.assertTrue(client.stopped)

    def test_scan_does_not_touch_workspace_config_or_hooks(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = init_repo(root / "repo")
        (repo / "tracked.txt").write_text("keep\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "track")
        (repo / "workspace-only.txt").write_text("scratch\n", encoding="utf-8")
        gitdir = repo / ".git"
        hooks = gitdir / "hooks"
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook_mtime = hook.stat().st_mtime_ns
        config = gitdir / "config"
        config_mtime = config.stat().st_mtime_ns
        index = gitdir / "index"
        index_mtime = index.stat().st_mtime_ns
        home = root / "home"
        cfg = website_fixture_config(home, repo, extra_root=root)
        home.mkdir()
        save_config(cfg)
        self.assertEqual(main(["--home", str(home), "init"]), 0)
        self.assertEqual(main(["--home", str(home), "scan", "--project", "website"]), 0)
        self.assertEqual(index.stat().st_mtime_ns, index_mtime)
        self.assertEqual(config.stat().st_mtime_ns, config_mtime)
        self.assertEqual(hook.stat().st_mtime_ns, hook_mtime)
        self.assertEqual((repo / "workspace-only.txt").read_text(encoding="utf-8"), "scratch\n")
        status = git(repo, "status", "--porcelain").stdout
        self.assertIn("?? workspace-only.txt", status)


class GitReadonlyBusinessContractTests(unittest.TestCase):
    def test_scan_does_not_touch_index(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = init_repo(root / "repo")
        git(repo, "status", "--porcelain")
        index = repo / ".git" / "index"
        before = index.stat().st_mtime_ns
        home = root / "home"
        cfg = website_fixture_config(home, repo, extra_root=root)
        home.mkdir()
        save_config(cfg)
        self.assertEqual(main(["--home", str(home), "init"]), 0)
        self.assertEqual(main(["--home", str(home), "scan", "--project", "website"]), 0)
        self.assertEqual(main(["--home", str(home), "scan", "--project", "website"]), 0)
        after = index.stat().st_mtime_ns
        self.assertEqual(before, after)
        status = git(repo, "status", "--porcelain").stdout
        self.assertEqual(status.strip(), "")
