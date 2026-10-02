"""Actual SDK/stdio processes, without calling Codex/Claude models or live services."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP SDK is not installed")
class DocumentMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_stdio_clients_save_resume_append_export_and_retry(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        from mcp.types import Implementation

        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        home = root / "shared"
        def client(source):
            return Client(StdioServerParameters(command=sys.executable,
                          args=["-m", "xiaomao", "--home", str(home), "docs", "serve", "--source", source],
                          env={"PATH": os.environ.get("PATH", ""), "HOME": str(root),
                               "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}),
                          client_info=Implementation(name=f"fixture-{source}", version="1.0"),
                          read_timeout_seconds=15)
        async with client("codex") as codex:
            tools = await codex.list_tools()
            self.assertEqual(len(tools.tools), 7)
            annotations = {t.name: t.annotations for t in tools.tools}
            self.assertFalse(annotations["save_document"].read_only_hint)
            self.assertTrue(annotations["read_document"].read_only_hint)
            payload = {"title": "重试策略", "content": "# 方案\n需要保留次数上限。", "request_id": "fixture-save"}
            saved = await codex.call_tool("save_document", payload)
            self.assertFalse(saved.is_error, saved)
            receipt = saved.structured_content
            self.assertTrue(receipt["saved"])
            self.assertEqual(receipt, (await codex.call_tool("save_document", payload)).structured_content)
            doc_id = receipt["document_id"]

        # A new independent process reads the durable record after the first exits.
        async with client("claude-code") as claude:
            found = await claude.call_tool("find_documents", {"query": "次数上限"})
            self.assertEqual(found.structured_content["documents"][0]["document_id"], doc_id)
            original = await claude.call_tool("read_document", {"document_id": doc_id})
            self.assertEqual(original.structured_content["content"], payload["content"])
            appended = await claude.call_tool("append_handoff", {"document_id": doc_id,
                    "content": "实现草稿已保存，测试仍未运行。", "request_id": "fixture-append", "expected_revision": 1})
            self.assertFalse(appended.is_error, appended)
            self.assertEqual(appended.structured_content["revision"], 2)
            stale = await claude.call_tool("append_handoff", {"document_id": doc_id,
                    "content": "cannot overwrite", "request_id": "stale", "expected_revision": 1})
            self.assertTrue(stale.is_error)
            exported = await claude.call_tool("export_document", {"document_id": doc_id})
            body = Path(exported.structured_content["path"]).read_text()
            self.assertIn(payload["content"], body)
            self.assertIn("测试仍未运行", body)
            self.assertIn("claude-code", body)
            self.assertIn("codex", body)
            original = await claude.call_tool("read_document", {"document_id": doc_id, "revision": 1})
            self.assertEqual(original.structured_content["content"], payload["content"])
        self.assertEqual(list(root.iterdir()), [home])

    async def test_tools_reject_scope_and_return_honest_errors(self):
        from mcp import Client
        from xiaomao.document_mcp import build_server
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        async with Client(build_server(root, source="codex", allowed_projects=("website",))) as client:
            failed = await client.call_tool("save_document", {"title": "x", "content": "y", "request_id": "a"})
            self.assertTrue(failed.is_error)
            self.assertFalse((root / "xiaomao.sqlite").exists())
            result = await client.call_tool("handoff_template")
            self.assertIn("验证情况", result.structured_content["template"])
