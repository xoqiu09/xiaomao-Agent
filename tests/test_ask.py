"""xiaomao ask: answers only from stored daily records, with cited days."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from tests.helpers import git, init_repo, website_fixture_config
from tests.plain_scenario import NOW, build, make_day, scan
from xiaomao.ask import ask_command, build_packet, recent_bundles, validated
from xiaomao.config import save_config
from xiaomao.daily_jobs import run_daily
from xiaomao.store import open_db, open_db_readonly, set_meta


class FakeClient:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        return {"json": self.reply}


class AskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cfg, cls.repos = build(Path(cls._tmp.name))
        cls.cfg.timezone = "Asia/Taipei"
        save_config(cls.cfg)
        make_day(cls.cfg, cls.repos)
        with patch("xiaomao.collect.utc_now", return_value=NOW.isoformat()):
            run_daily(cls.cfg, date="2026-10-02", now=NOW)
        cls.home = Path(cls.cfg.home)
        cls.db = cls.home / "xiaomao.sqlite"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def ask(self, question="xiuqiu-site 做了什么", reply=None, **opts):
        ns = Namespace(question=question, days=opts.pop("days", 7), project=opts.pop("project", None),
                       model=opts.pop("model", None))
        client = FakeClient(reply if reply is not None else
                            {"answer": "xiuqiu-site 加了空结果提示，还在本机。", "sources": ["2026-10-02"],
                             "not_in_records": False})
        out, err = io.StringIO(), io.StringIO()
        code = ask_command(ns, self.home, client_factory=lambda cfg: client, today="2026-10-03",
                           out=out, err=err, **opts)
        return code, out.getvalue(), err.getvalue(), client

    def packet(self, project=None):
        with open_db_readonly(self.db) as conn:
            return build_packet(recent_bundles(conn, self.cfg, days=7, until="2026-10-03"), project)

    def test_packet_holds_only_stored_report_facts(self):
        days = self.packet()
        self.assertEqual([d["date"] for d in days], ["2026-10-02"])
        text = json.dumps(days, ensure_ascii=False)
        self.assertIn("空结果提示", text)
        self.assertIn("🟠 仅本机", text)
        self.assertIn("旷野之心", days[0]["no_change"])
        # No paths, SHAs or file names reach the model.
        for repo in self.repos.values():
            self.assertNotIn(str(repo), text)
        self.assertNotIn(str(self.home), text)
        self.assertNotRegex(text, r"[0-9a-f]{40}")

    def test_project_filter(self):
        days = self.packet("笔记工具")
        self.assertEqual([p["project"] for p in days[0]["projects"]], ["笔记工具"])
        self.assertEqual([s["project"] for s in days[0]["status"]], ["笔记工具"])
        self.assertTrue(days[0]["status"][0]["no_remote"])
        self.assertTrue(all(s.startswith("笔记工具") for s in days[0]["next_steps"]))
        self.assertNotIn("xiuqiu-site", days[0]["summary"])
        self.assertTrue(days[0]["summary"].startswith("今天 1 个项目有进展"))

    def test_revoked_window_is_never_sent_to_model(self):
        from dataclasses import replace
        save_config(replace(self.cfg, projects=[p for p in self.cfg.projects if p.project_id != "website"]))
        try:
            code, _out, _err, client = self.ask(project="website")
            self.assertEqual((code, len(client.calls)), (2, 0))
            _code, _out, _err, client = self.ask()
            self.assertEqual(json.loads(client.calls[0]["user"])["records"], [])
        finally:
            save_config(self.cfg)

    def test_window_scope_and_date_must_match_row_metadata(self):
        import sqlite3
        with sqlite3.connect(":memory:") as conn, open_db_readonly(self.db) as original:
            conn.execute("CREATE TABLE daily_windows(date, scope_hash, bundle_json, completed_at)")
            date, scope_hash, raw, finished = original.execute(
                "SELECT date, scope_hash, bundle_json, completed_at FROM daily_windows").fetchone()
            conn.execute("INSERT INTO daily_windows VALUES(?,?,?,?)", (date, scope_hash, raw, finished))
            conn.execute("INSERT INTO daily_windows VALUES(?,?,?,?)", (date, "other", "{", finished))
            self.assertEqual(len(recent_bundles(conn, self.cfg, days=7, until=date)), 1)
            bundle = json.loads(raw)
            for bad in (dict(bundle, scope=[]), dict(bundle, window={"date": "2000-01-01"})):
                conn.execute("UPDATE daily_windows SET bundle_json=? WHERE scope_hash=?", (json.dumps(bad), scope_hash))
                self.assertEqual(recent_bundles(conn, self.cfg, days=7, until=date), [])

    def test_status_covers_projects_without_changes_that_day(self):
        """10/31: mini-exchange did not change but still held 5 unpushed commits."""
        def project(name, changed, unpushed=(), commits=None):
            return {"project_id": name, "display_name": name, "repo_id": name, "changed": changed,
                    "commits": [], "ongoing": [], "activity": [], "trees": [],
                    "delivery_state": {"has_remote": True, "default_ref": "refs/remotes/origin/main",
                                       "unpushed": list(unpushed), "dirty_files": 0, "commits": commits or {}}}
        bundle = {"window": {"date": "2026-10-31"}, "projects": [
            project("mini-exchange", False, unpushed=["a" * 40] * 5),
            project("Session Steward", True, commits={"b" * 40: "pushed"}),
            project("QAI", False)]}
        status = {s["project"]: s for s in build_packet([bundle])[0]["status"]}
        self.assertEqual(status["mini-exchange"]["unpushed_commits"], 5)
        self.assertEqual((status["Session Steward"]["unpushed_commits"],
                          status["Session Steward"]["pushed_not_merged_in_window"]), (0, 1))
        self.assertNotIn("QAI", status)                                   # nothing pending
        self.assertEqual(self.packet("不存在的项目"), [])

    def test_pushed_not_merged_daily_count_is_not_the_project_total(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = init_repo(root / "repo")
        home = root / "home"
        cfg = website_fixture_config(home, repo)
        cfg.timezone = "Asia/Taipei"
        save_config(cfg)
        git(repo, "remote", "add", "origin", str(root / "remote.git"))
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        scan(cfg, "2026-11-24T14:00:00+00:00")

        git(repo, "switch", "-c", "export")
        (repo / "notes.py").write_text("def export_notes():\n    return 'notes'\n")
        git(repo, "add", "notes.py")
        at = "2026-11-25T03:00:00+00:00"
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": at, "GIT_AUTHOR_DATE": at}):
            git(repo, "commit", "-m", "Add note export")
        git(repo, "update-ref", "refs/remotes/origin/export", "HEAD")

        for day in ("2026-11-25", "2026-11-26"):
            if day == "2026-11-26":
                (repo / "draft.py").write_text("draft = True\n")
            scan(cfg, f"{day}T07:00:00+00:00")
            now = datetime.fromisoformat(f"{day}T14:00:00+00:00")
            with patch("xiaomao.collect.utc_now", return_value=now.isoformat()):
                run_daily(cfg, date=day, now=now)

        ns = Namespace(question="现在还有几条已推送、未合并的提交？", days=2, project="website", model=None)
        client = FakeClient({"answer": "资料不够。", "sources": ["2026-11-25"]})
        code = ask_command(ns, home, client_factory=lambda cfg: client, today="2026-11-26",
                           out=io.StringIO(), err=io.StringIO())
        self.assertEqual(code, 0)
        first, second = json.loads(client.calls[0]["user"])["records"]
        self.assertEqual(first["projects"][0]["commits"], [{"title": "Add note export", "where": "🔵 已推送"}])
        self.assertEqual(second["projects"][0]["commits"], [])
        statuses = [day["status"][0] for day in (first, second)]
        self.assertEqual([s.get("pushed_not_merged_in_window") for s in statuses], [1, 0], statuses)
        self.assertIn("pushed_not_merged_total", statuses[1])
        self.assertIsNone(statuses[1]["pushed_not_merged_total"])

    def test_previous_push_stays_visible_on_a_clean_day_or_after_removing_local_remote(self):
        project = {"project_id": "website", "repo_id": "site", "display_name": "笔记站", "changed": False,
                   "commits": [], "ongoing": [], "activity": [], "trees": []}
        yesterday = {"window": {"date": "2026-11-25"}, "projects": [dict(project, changed=True,
                     delivery_state={"has_remote": True, "commits": {"a" * 40: "pushed"}})]}
        for has_remote, dirty_files in ((True, 0), (False, 1), (False, 0), (None, None)):
            with self.subTest(has_remote=has_remote, dirty_files=dirty_files):
                delivery = {"has_remote": has_remote, "commits": {}, "dirty_files": dirty_files} if has_remote is not None else {}
                today = {"window": {"date": "2026-11-26"}, "projects": [dict(project, delivery_state=delivery)]}
                first, second = build_packet([yesterday, today], project="website")
                self.assertEqual(first["status"][0]["pushed_not_merged_in_window"], 1)
                self.assertEqual([
                    (s["project"], s["pushed_not_merged_in_window"], s["pushed_not_merged_total"], s["uncommitted_files"])
                    for s in second["status"]
                ], [("笔记站", 0, None, dirty_files)])

    def test_answer_names_its_days(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        code, out, _err, client = self.ask()
        self.assertEqual(code, 0)
        self.assertIn("xiuqiu-site 加了空结果提示", out)
        self.assertIn("依据：10月2日的晚报", out)
        self.assertIn("xiuqiu-site 做了什么", client.calls[0]["user"])
        self.assertEqual(client.calls[0]["model"], self.cfg.ask_model)                  # general model by default
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)   # nothing written

    def test_answer_uses_daily_report_when_handoff_library_is_locked(self):
        library = Path(self.enterContext(tempfile.TemporaryDirectory()))
        save_config(replace(self.cfg, docs_home=str(library)))
        self.addCleanup(save_config, self.cfg)
        with patch("xiaomao.documents.DocumentStore.list", side_effect=sqlite3.OperationalError("database is locked")):
            code, out, err, client = self.ask("那天的晚报怎么说")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(client.calls), 1)
        sent = json.loads(client.calls[0]["user"])
        self.assertEqual([record["date"] for record in sent["records"]], ["2026-10-02"])
        self.assertEqual(sent["handoffs"], [])
        self.assertIn("依据：10月2日的晚报", out)
        self.assertNotIn("交接《", out)
        self.assertIn("本次没有读到交接文档", out)

    def test_answer_without_valid_days_is_not_shown(self):
        code, out, err, _ = self.ask(reply={"answer": "做了很多。", "sources": ["2026-09-01"],
                                            "not_in_records": False})
        self.assertEqual((code, out), (3, ""))
        self.assertIn("没有注明依据", err)
        self.assertIsNone(validated({"json": {"answer": "", "sources": [], "not_in_records": True}}, {"2026-10-02"}))

    def test_answer_with_any_missing_cited_day_is_not_shown(self):
        code, out, err, _ = self.ask(reply={
            "kind": "project",
            "answer": "2 日加了空结果提示，3 日已发布。",
            "sources": ["2026-10-02", "2026-10-03"],
            "not_in_records": False,
            "handoff_question": "",
        })
        self.assertEqual((code, out), (3, ""))
        self.assertIn("没有注明依据", err)

    def test_not_in_records_ignores_the_models_claim(self):
        for sources in ([], ["2026-10-02"]):
            with self.subTest(sources=sources):
                code, out, _err, _ = self.ask("上个月部署了吗", reply={
                    "kind": "project",
                    "answer": "样例项目已经部署。",
                    "sources": sources,
                    "not_in_records": True,
                    "handoff_question": "",
                })
                self.assertEqual(code, 0)
                self.assertEqual(out, "现有资料里没有这项记录。\n")

    def test_model_failure_and_paused_inference(self):
        class Broken:
            def generate_json(self, **kwargs):
                raise TimeoutError("slow")
        ns = Namespace(question="q", days=7, project=None, model=None)
        err = io.StringIO()
        code = ask_command(ns, self.home, client_factory=lambda cfg: Broken(), today="2026-10-03",
                           out=io.StringIO(), err=err)
        self.assertEqual(code, 3)
        self.assertIn("本地模型调用失败", err.getvalue())
        with open_db(self.db) as conn:
            set_meta(conn, "infer_paused", "1")
        try:
            code, _out, err_text, client = self.ask()
        finally:
            with open_db(self.db) as conn:
                set_meta(conn, "infer_paused", "0")
        self.assertEqual(code, 3)
        self.assertIn("推理已暂停", err_text)
        self.assertEqual(client.calls, [])

    def test_nonlocal_ollama_is_rejected_before_lookup_or_prompt(self):
        save_config(replace(self.cfg, ollama_host="model.example.invalid:11434"))
        ns = Namespace(question="笔记工具做了什么", days=7, project=None, model=None)
        out, err = io.StringIO(), io.StringIO()
        try:
            with (
                patch("xiaomao.ollama_runtime.models_dir_allowed"),
                patch("xiaomao.ollama_runtime.running_ollama_pids", return_value=[]),
                patch("xiaomao.ollama_runtime.start_serve") as start,
                patch("socket.create_connection", side_effect=OSError("offline")) as connect,
            ):
                code = ask_command(ns, self.home, today="2026-10-03", out=out, err=err)
                self.assertEqual(code, 3)
                connect.assert_not_called()
                start.assert_not_called()
        finally:
            save_config(self.cfg)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("Ollama 地址必须指向本机", err.getvalue())

    def test_shared_ollama_client_rejects_nonlocal_prompt(self):
        from xiaomao.ollama_runtime import OllamaClient

        client = OllamaClient(replace(self.cfg, ollama_host="model.example.invalid:11434"))
        with (
            patch("xiaomao.ollama_runtime.models_dir_allowed"),
            patch("socket.create_connection", side_effect=OSError("offline")) as connect,
        ):
            with self.assertRaisesRegex(ValueError, "Ollama 地址必须指向本机"):
                client.generate_json(model=self.cfg.ask_model, system="", user="虚构项目资料", schema={})
            connect.assert_not_called()

    def test_ollama_requests_connect_directly_even_with_proxy(self):
        from xiaomao.ollama_runtime import api_get, api_post

        connection = Mock()
        connection.makefile.side_effect = lambda *args: io.BytesIO(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"
        )
        for configured, host, port in (
            ("127.0.0.1:11434", "127.0.0.1", 11434),
            ("127.0.0.2:11434", "127.0.0.2", 11434),
            ("[::1]:11434", "::1", 11434),
            ("::1:11435", "::1", 11435),
            ("[0:0:0:0:0:0:0:1]:11434", "::1", 11434),
        ):
            with (
                self.subTest(configured=configured),
                patch("urllib.request._opener", None),
                patch("urllib.request.getproxies", return_value={"http": "http://proxy.example.invalid:8123"}),
                patch("urllib.request.proxy_bypass", return_value=False),
                patch("socket.create_connection", return_value=connection) as connect,
            ):
                cfg = replace(self.cfg, ollama_host=configured)
                self.assertEqual(api_get(cfg, "/api/tags"), {})
                self.assertEqual(api_post(cfg, "/api/chat", {"message": "虚构项目资料"}), {})
                self.assertEqual([call.args[0] for call in connect.call_args_list], [(host, port)] * 2)

    def test_ollama_requests_do_not_follow_redirects(self):
        from xiaomao.ollama_runtime import api_get, api_post

        connection = Mock()
        connection.makefile.side_effect = lambda *args: io.BytesIO(
            b"HTTP/1.1 302 Found\r\nLocation: http://other.example.invalid/api\r\nContent-Length: 0\r\n\r\n"
        )
        with (
            patch("urllib.request._opener", None),
            patch("urllib.request.getproxies", return_value={}),
            patch("socket.create_connection", return_value=connection) as connect,
        ):
            self.assertIsNone(api_get(self.cfg, "/api/tags"))
            with self.assertRaises(HTTPError) as error:
                api_post(self.cfg, "/api/chat", {"message": "虚构项目资料"})
            self.assertEqual(error.exception.code, 302)
            self.assertEqual([call.args[0] for call in connect.call_args_list], [("127.0.0.1", 11434)] * 2)

    def test_only_registered_models_and_no_records(self):
        code, _out, err, client = self.ask(model="some-cloud-model")
        self.assertEqual(code, 2)
        self.assertIn("只能使用配置里登记的本地模型", err)
        self.assertEqual(client.calls, [])
        ns = Namespace(question="q", days=3, project="xiuqiu-site", model=None)
        err = io.StringIO()
        self.assertEqual(ask_command(ns, self.home, client_factory=lambda cfg: None, today="2026-11-01",
                                     out=io.StringIO(), err=err), 2)
        self.assertIn("最近 3 天还没有这个项目的晚报记录", err.getvalue())

    def test_general_answer_is_labelled_and_unsure_one_is_handed_off(self):
        code, out, _err, _ = self.ask("什么是 rebase", reply={
            "kind": "general", "answer": "rebase 把一串提交搬到另一个起点上重新提交。", "sources": [],
            "not_in_records": False, "handoff_question": ""})
        self.assertEqual(code, 0)
        self.assertIn("这是模型自己的回答，没有依据小猫的记录", out)
        code, out, _err, _ = self.ask("这段代码有没有并发问题", reply={
            "kind": "defer", "answer": "要看代码才能判断。", "sources": [], "not_in_records": False,
            "handoff_question": "请检查 xiuqiu-site 的筛选组件是否有并发问题"})
        self.assertEqual(code, 0)
        self.assertIn("建议去问 Codex 或 Claude，可以直接粘贴：\n请检查 xiuqiu-site 的筛选组件是否有并发问题", out)
        code, _out, err, _ = self.ask(reply={"kind": "defer", "answer": "不确定", "sources": [],
                                             "not_in_records": False, "handoff_question": ""})
        self.assertEqual(code, 3)                                         # a handoff needs the question

    def test_project_briefs_and_handoffs_are_bounded_sources(self):
        from xiaomao.documents import DocumentStore
        from xiaomao.briefing import BriefingSource, briefing_path_for, write_cache
        for project_id, purpose in [("website", "个人网站，展示简报与学习内容。"), ("wallet", "见 src/wallet/core.ts 文件")]:
            source = BriefingSource(project_id, briefing_path_for(self.cfg, project_id), "", "a" * 64, 0)
            write_cache(self.cfg, source, {"purpose": purpose, "identities": [{"note": "/Users/x/secret"}]},
                        model=self.cfg.ask_model, chunk_count=1)
        library = Path(self.enterContext(tempfile.TemporaryDirectory()))
        doc = DocumentStore(library).save(title="阶段一交接", content="下一步：补测试。" * 400,
                                          source="claude-code", request_id="req-1")
        self.cfg.docs_home = str(library)
        save_config(self.cfg)
        try:
            code, out, _err, client = self.ask("xiuqiu-site 是做什么的", reply={
                "kind": "project", "answer": "它是个人网站。", "sources": ["brief:website", f"doc:{doc['document_id']}"],
                "not_in_records": False, "handoff_question": ""})
        finally:
            self.cfg.docs_home = ""
            save_config(self.cfg)
        self.assertEqual(code, 0)
        self.assertIn("依据：xiuqiu-site 的项目说明、交接《阶段一交接》", out)
        sent = json.loads(client.calls[0]["user"])
        self.assertEqual([b["source"] for b in sent["project_briefs"]], ["brief:website"])   # path-bearing purpose dropped
        self.assertNotIn("/Users/x/secret", client.calls[0]["user"])
        self.assertLessEqual(len(sent["handoffs"][0]["content"]), 1500)

    def test_brief_digest_does_not_follow_old_registration_or_read_raw_document(self):
        from dataclasses import replace
        from xiaomao.ask import project_briefs
        from xiaomao.briefing import BriefingSource, briefing_path_for, cache_paths, write_cache
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(self.cfg, home=tmp)
            spec = cfg.project("website")
            source = BriefingSource("website", briefing_path_for(cfg, "website"), "", "a" * 64, 0)
            write_cache(cfg, source, {"purpose": "原来的用途"}, model=cfg.ask_model, chunk_count=1)
            with patch("xiaomao.briefing.load_source", side_effect=AssertionError("read source")):
                self.assertEqual(project_briefs(cfg, "website")[0]["purpose"], "原来的用途")
                moved = replace(cfg, projects=[replace(spec, approved_root=tmp)])
                self.assertEqual(project_briefs(moved, "website"), [])
                self.assertEqual(project_briefs(replace(cfg, briefing_docs_root=tmp), "website"), [])
                body, meta = cache_paths(cfg, "website")
                body.write_text(body.read_text().replace("原来的用途", "错误的用途"))
                self.assertEqual(project_briefs(cfg, "website"), [])
                meta.unlink()
                self.assertEqual(project_briefs(cfg, "website"), [])

    def test_menu_offers_the_ask_dialog(self):
        from xiaomao.swiftbar import render_menu
        line = next(l for l in render_menu(self.home).splitlines() if l.startswith("问小猫…"))
        self.assertIn("ask-dialog.py", line)
        self.assertIn("terminal=false", line)

    def test_dialog_passes_text_only_as_argv(self):
        import importlib.util
        import base64
        spec = importlib.util.spec_from_file_location(
            "ask_dialog", Path(__file__).resolve().parents[1] / "scripts" / "ask-dialog.py")
        dialog = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dialog)
        calls = []

        def fake_osascript(script, *args):
            calls.append((script, args))
            reply = 'x"; do shell script "rm -rf ~"' if "default answer" in script else "好"
            return type("P", (), {"returncode": 0, "stdout": reply})()
        payload = base64.urlsafe_b64encode(json.dumps([str(self.home)]).encode()).decode()
        seen = {}

        def fake_ask(ns, home, out, err):
            seen["q"] = ns.question
            out.write("ANSWER-TEXT")
            return 0
        with patch.object(dialog, "osascript", fake_osascript), patch("xiaomao.ask.ask_command", fake_ask):
            self.assertEqual(dialog.main(["ask-dialog.py", payload]), 0)
        self.assertEqual(seen["q"], 'x"; do shell script "rm -rf ~"')     # data, never script text
        self.assertTrue(all(arg not in script for script, args in calls for arg in args))
        self.assertEqual(calls[-1][1], ("ANSWER-TEXT",))


if __name__ == "__main__":
    unittest.main()
