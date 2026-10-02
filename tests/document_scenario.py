"""Reproducible MCP handoff specimen; client labels are fixtures, not real apps."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path


async def scenario(output: Path):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    home = output / "document-home"
    home.mkdir(mode=0o700)
    (home / "config.json").write_text(json.dumps({"projects": []}))
    transcript = []

    def client(source):
        return Client(StdioServerParameters(
            command=sys.executable,
            args=["-m", "xiaomao", "--home", str(home), "docs", "serve", "--source", source],
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(output),
                 "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}), read_timeout_seconds=15)

    async def call(client, name, args):
        result = await client.call_tool(name, args)
        if result.is_error or result.structured_content is None:
            raise RuntimeError(f"scenario tool failed: {name}")
        transcript.append({"tool": name, "result": result.structured_content})
        return result.structured_content

    original = ("# 文档交接协议验收样例\n\n这是隔离测试材料，来源名称是测试标签；"
                "没有调用真实 Codex 或 Claude Code 模型。\n\n"
                "## 关键结论\n登录失败后最多重试三次，每次间隔需要保留上限。\n\n"
                "## 待决定\n退避策略由用户确认。\n")
    checkpoint = ("## 阶段说明\n实现草稿已经保存。\n\n"
                  "## 未验证部分\n失败场景尚未运行测试，不代表可发布。\n\n"
                  "## 下一步\n确认退避策略后补充失败场景；保留之前的次数上限。\n")
    async with client("codex") as first:
        args = {"title": "文档交接样例（测试材料）", "content": original,
                "request_id": "scenario-save", "source_ref": "fixture://original"}
        saved = await call(first, "save_document", args)
        duplicate = await call(first, "save_document", args)
        assert duplicate == saved
    doc_id = saved["document_id"]
    async with client("claude-code") as second:
        found = await call(second, "find_documents", {"query": "次数上限"})
        assert found["total"] == 0  # Exact keyword search; no invented semantic matches.
        found = await call(second, "find_documents", {"query": "重试三次"})
        assert found["documents"][0]["document_id"] == doc_id
        read = await call(second, "read_document", {"document_id": doc_id})
        assert read["content"] == original
        await call(second, "append_handoff", {"document_id": doc_id, "content": checkpoint,
                   "request_id": "scenario-append", "expected_revision": 1, "source_ref": "fixture://checkpoint"})
        exported = await call(second, "export_document", {"document_id": doc_id})
        history = await call(second, "document_history", {"document_id": doc_id})
        assert len(history["revisions"]) == 2
    body = Path(exported["path"]).read_text()
    assert original in body and checkpoint in body
    (output / "sample-handoff.md").write_text(body)
    (output / "receipts.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
    for source, filename in (("codex", "codex-mcp.toml"), ("claude-code", "claude-mcp.json")):
        proc = subprocess.run([sys.executable, "-m", "xiaomao", "--home", str(home), "docs", "connection", "--client", source],
                              text=True, capture_output=True, check=True, timeout=15)
        (output / filename).write_text(proc.stdout)
    verdict = {"ok": True, "mode": "official SDK + two real stdio subprocesses; fixture client labels",
               "document_id": doc_id, "revision_count": 2, "model_calls": 0,
               "real_codex_client": "NOT_RUN", "real_claude_code_client": "NOT_RUN",
               "export": exported, "sample": str(output / "sample-handoff.md")}
    (output / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2))
    return verdict


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True, help="A new directory; never overwrites an earlier scenario")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(scenario(args.output)), ensure_ascii=False, indent=2))
