from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from xiaomao.cli import main
from xiaomao.config import save_config
from xiaomao.documents import DocumentStore, MAX_DOCUMENT_BYTES, read_document_file
from xiaomao.store import open_db
from tests.helpers import website_fixture_config


class DocumentsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.cfg = website_fixture_config(self.home, self.repo, extra_root=self.root)
        save_config(self.cfg)
        self.store = DocumentStore(self.home)

    def save(self, **kw):
        args = dict(title="登录方案", content="# 方案\n采用有限次重试。\n",
                    source="codex", project_id="website", request_id="save-1")
        args.update(kw)
        return self.store.save(**args)

    def test_save_reopen_append_and_export_preserve_both_sources(self):
        saved = self.save()
        other = DocumentStore(self.home)
        doc_id = saved["document_id"]
        self.assertEqual(other.read(doc_id)["content"], "# 方案\n采用有限次重试。\n")
        note = other.append(doc_id, content="实现草稿已保存；失败场景尚未验证。", source="claude-code",
                            expected_revision=1, request_id="append-1")
        self.assertEqual(note["revision"], 2)
        self.assertEqual(other.read(doc_id)["kind"], "handoff")
        self.assertEqual(other.read(doc_id, revision=1)["source"], "codex")
        self.assertEqual(len(other.history(doc_id)["revisions"]), 2)
        exported = other.export(doc_id)
        body = Path(exported["path"]).read_text()
        self.assertIn("采用有限次重试", body)
        self.assertIn("失败场景尚未验证", body)
        self.assertIn("claude-code", body)
        self.assertIn("来源自述", body)
        self.assertIn("保存内容：提交全文", body)
        self.assertIn("保存内容：Agent 交接摘要", body)
        self.assertEqual(exported, other.export(doc_id))

    def test_retry_is_idempotent_and_key_collision_is_rejected(self):
        saved = self.save()
        self.assertEqual(saved, self.save())
        with self.assertRaisesRegex(ValueError, "请求.*不同"):
            self.save(content="另一份内容")
        self.assertEqual(len(self.store.list()["documents"]), 1)

    def test_append_retry_before_revision_check_and_stale_write_rejected(self):
        doc_id = self.save()["document_id"]
        args = dict(content="下一步：补充样例", source="claude-code", expected_revision=1, request_id="a1")
        first = self.store.append(doc_id, **args)
        self.assertEqual(first, self.store.append(doc_id, **args))
        with self.assertRaisesRegex(ValueError, "版本"):
            self.store.append(doc_id, **{**args, "request_id": "a2"})
        self.assertEqual(len(self.store.history(doc_id)["revisions"]), 2)

    def test_concurrent_append_cannot_overwrite_a_handoff(self):
        doc_id = self.save()["document_id"]
        def append(n):
            try:
                return DocumentStore(self.home).append(doc_id, content=f"进度 {n}", source="fixture",
                                                       expected_revision=1, request_id=f"parallel-{n}")
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(append, [1, 2]))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(len(self.store.history(doc_id)["revisions"]), 2)

    def test_long_unicode_document_can_be_read_completely_in_pages(self):
        content = "前提很重要。\n" * 5000
        doc_id = self.save(content=content)["document_id"]
        parts, offset = [], 0
        while True:
            result = self.store.read(doc_id, offset=offset, max_chars=1000)
            parts.append(result["content"])
            if result["next_offset"] is None:
                break
            offset = result["next_offset"]
        self.assertEqual("".join(parts), content)
        self.assertEqual(result["total_chars"], len(content))

    def test_search_content_and_pagination_keep_separate_documents(self):
        a = self.save()["document_id"]
        b = self.save(request_id="save-2")["document_id"]
        self.assertNotEqual(a, b)
        self.assertEqual(len(self.store.list(query="有限次")["documents"]), 2)
        page = self.store.list(limit=1)
        self.assertEqual(page["next_offset"], 1)
        self.assertEqual(len(self.store.list(offset=1)["documents"]), 1)
        self.assertEqual(self.store.list(query="%_")["documents"], [])

    def test_revoked_scope_hides_documents_and_blocks_read_append_export(self):
        doc_id = self.save()["document_id"]
        self.cfg.projects = []
        save_config(self.cfg)
        self.assertEqual(self.store.list()["documents"], [])
        for call in (lambda: self.store.read(doc_id), lambda: self.store.history(doc_id),
                     lambda: self.store.export(doc_id), lambda: self.store.append(
                         doc_id, content="x", source="fixture", expected_revision=1, request_id="a")):
            with self.assertRaises(ValueError):
                call()

    def test_rebinding_project_root_does_not_relabel_old_material(self):
        doc_id = self.save()["document_id"]
        self.cfg.projects[0].approved_root = str(self.root / "replacement")
        save_config(self.cfg)
        self.assertEqual(self.store.list()["documents"], [])
        with self.assertRaises(ValueError):
            self.store.read(doc_id)

    def test_server_project_boundaries_and_general_documents(self):
        doc_id = self.save(project_id=None)["document_id"]
        restricted = DocumentStore(self.home, allowed_projects=("website",))
        self.assertEqual(restricted.list()["documents"], [])
        with self.assertRaises(ValueError):
            restricted.read(doc_id)
        with self.assertRaises(ValueError):
            restricted.save(title="x", content="y", source="z", request_id="a")
        with self.assertRaises(ValueError):
            self.save(project_id="unknown", request_id="b")

    def test_read_queries_do_not_create_or_migrate_database(self):
        self.assertEqual(self.store.list()["documents"], [])
        self.assertFalse((self.home / "xiaomao.sqlite").exists())
        with sqlite3.connect(self.home / "xiaomao.sqlite") as conn:
            conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO meta VALUES ('schema','4')")
        self.assertEqual(self.store.list()["documents"], [])
        with sqlite3.connect(self.home / "xiaomao.sqlite") as conn:
            self.assertEqual(conn.execute("SELECT value FROM meta").fetchone()[0], "4")
            self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 1)

    def test_secrets_in_content_and_metadata_are_redacted_before_storage(self):
        secret = "ghp_" + "A" * 30
        saved = self.save(title=secret, content=f"# safe\nAPI_KEY=private-value\n{secret}\nlast line",
                          source_ref=f"https://example.invalid/{secret}")
        text = self.store.read(saved["document_id"])
        self.assertTrue(text["redacted"])
        self.assertIn("last line", text["content"])
        with sqlite3.connect(self.home / "xiaomao.sqlite") as conn:
            dump = "\n".join(conn.iterdump())
        self.assertNotIn(secret, dump)
        self.assertNotIn("private-value", dump)

    def test_invalid_input_and_budget_failure_do_not_claim_saved(self):
        for content in ("", "x\0y", "x" * (MAX_DOCUMENT_BYTES + 1)):
            with self.assertRaises(ValueError):
                self.save(content=content)
        with patch("xiaomao.documents.dir_size_bytes", return_value=2**40):
            with self.assertRaisesRegex(ValueError, "预算"):
                self.save()
        self.assertEqual(self.store.list()["documents"], [])

    def test_symlink_database_and_export_directory_are_rejected(self):
        outside = self.root / "outside.sqlite"
        with sqlite3.connect(outside) as conn:
            conn.execute("CREATE TABLE sentinel(x)")
        (self.home / "xiaomao.sqlite").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.store.list()
        with self.assertRaises(ValueError):
            self.save()
        (self.home / "xiaomao.sqlite").unlink()
        doc_id = self.save()["document_id"]
        external = self.root / "external-export"
        external.mkdir()
        (self.home / "reports").mkdir(exist_ok=True)
        (self.home / "reports" / "documents").symlink_to(external)
        with self.assertRaises(ValueError):
            self.store.export(doc_id)
        self.assertEqual(list(external.iterdir()), [])

    def test_export_refuses_tampered_existing_snapshot(self):
        doc_id = self.save()["document_id"]
        exported = self.store.export(doc_id)
        dest = Path(exported["path"])
        dest.write_text("changed externally")
        with self.assertRaises(ValueError):
            self.store.export(doc_id)
        self.assertEqual(dest.read_text(), "changed externally")

    def test_source_file_is_bounded_utf8_regular_document(self):
        source = self.root / "note.md"
        source.write_text("original")
        self.assertEqual(read_document_file(source), "original")
        alias = self.root / "alias.md"
        alias.symlink_to(source)
        with self.assertRaises(ValueError):
            read_document_file(alias)
        source.write_bytes(b"bad\x00text")
        with self.assertRaises(ValueError):
            read_document_file(source)
        with self.assertRaises(ValueError):
            read_document_file(self.root / ".env")

    def test_cli_document_flow_uses_same_store_and_keeps_input_unchanged(self):
        source = self.root / "report.md"
        source.write_text("# 当前进展\n下一步：回归。\n")
        output = io.StringIO()
        with redirect_stdout(output):
            rc = main(["--home", str(self.home), "docs", "save", "--file", str(source),
                       "--title", "阶段交接", "--source", "codex", "--request-id", "cli-1"])
        self.assertEqual(rc, 0)
        receipt = json.loads(output.getvalue())
        self.assertEqual(self.store.read(receipt["document_id"])["content"], source.read_text())
        self.assertEqual(source.read_text(), "# 当前进展\n下一步：回归。\n")

    def test_additive_schema_migration_preserves_old_observations(self):
        with open_db(self.home / "xiaomao.sqlite") as conn:
            conn.execute("INSERT INTO user_notes VALUES ('old','website','then','keep','fixture')")
        self.save()
        with sqlite3.connect(self.home / "xiaomao.sqlite") as conn:
            self.assertEqual(conn.execute("SELECT confirmed_goal_or_decision FROM user_notes").fetchone()[0], "keep")
            self.assertEqual(conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], "5")

    def test_renamed_company_worktree_rejected_before_reading_document(self):
        disguised = self.root / "renamed"
        disguised.mkdir()
        (disguised / ".git").write_text("gitdir: " + str(self.root / "theaiapp-service" / ".git"))
        report = disguised / "report.md"
        report.write_text("must not read")
        with patch("xiaomao.documents.os.open", side_effect=AssertionError("document content read")):
            with self.assertRaises(ValueError):
                read_document_file(report)

    def test_config_symlink_and_secret_source_label_rejected(self):
        (self.home / "config.json").unlink()
        target = self.root / "private.json"
        target.write_text("must not read")
        (self.home / "config.json").symlink_to(target)
        with self.assertRaises(ValueError):
            self.store.list()
        (self.home / "config.json").unlink()
        with self.assertRaises(ValueError):
            self.save(project_id=None, source="ghp_" + "B" * 30)

    def test_connection_configs_share_home_and_use_argument_arrays(self):
        import tomllib
        configs = {}
        for client in ("codex", "claude-code"):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["--home", str(self.home), "docs", "connection", "--client", client]), 0)
            if client == "codex":
                configs[client] = tomllib.loads(out.getvalue())["mcp_servers"]["xiaomao-documents"]
            else:
                configs[client] = json.loads(out.getvalue())["mcpServers"]["xiaomao-documents"]
            self.assertIn(str(self.home), configs[client]["args"])
            self.assertEqual(configs[client]["args"][-1], client)
            self.assertTrue(Path(configs[client]["command"]).is_absolute())
        self.assertEqual(configs["codex"]["command"], configs["claude-code"]["command"])
        self.assertFalse((self.home / "xiaomao.sqlite").exists())

    def test_corrupted_database_body_refuses_read_and_export(self):
        doc_id = self.save()["document_id"]
        with sqlite3.connect(self.home / "xiaomao.sqlite") as conn:
            conn.execute("UPDATE document_revisions SET content='tampered'")
        with self.assertRaisesRegex(ValueError, "校验"):
            self.store.read(doc_id)
        with self.assertRaisesRegex(ValueError, "校验"):
            self.store.export(doc_id)
