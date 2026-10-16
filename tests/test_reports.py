from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from xiaomao.cli import main
from xiaomao.config import default_config, save_config
from xiaomao.eval_runner import host_pressure, self_check_validators
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
        daily = (home / "reports" / "daily" / "2026-09-20.txt").read_text(encoding="utf-8")
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
        self.assertEqual(con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], "4")
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
