from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from xiaomao.briefing import (
    cache_paths,
    coerce_understanding,
    extra_for_summarize,
    ingest_project,
    load_cached_understanding,
    load_source,
    split_chunks,
    write_cache,
)
from xiaomao.cli import main
from xiaomao.config import ProjectSpec, WorktreeSpec, load_config, save_config
from xiaomao.paths import ensure_layout
from xiaomao.policy import redact_secret_spans, sha256_bytes
from tests.helpers import init_repo, website_fixture_config


SAMPLE_DOC = """# website 项目说明

用途：给个人工程做只读观察。

## 三份源码必须分开看

原仓 HEAD abc123；候选未知；安装未知。

## 一条已核对的调用链

scan 只读 Git，不执行测试。

## 核心功能审查表

| 核心功能与使用场景 | 入口与关键流程 | 实现状态 | 验证状态 | 源码/测试依据 | 问题与边界 | 后续验证建议 |
| --- | --- | --- | --- | --- | --- | --- |
| 观察 Git 指纹 (`WEB-C01`) | scan | 已实现 | 仅静态核对 | [README.md](/elsewhere/README.md) | 静态审查不是运行通过 | 不要把 unknown 写成测试通过 |
| 部署发布 (`WEB-C02`) | — | 未实现 | 仅静态核对 | 无 | 没有部署证据 | 保持 unknown |

验证边界：本轮未部署、未跑测试。
"""


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.stopped: list[str] = []

    def generate_json(self, *, model, system, user, schema):
        self.calls.append({"model": model, "user": user, "system": system, "schema": schema})
        gaps = []
        if "仅静态核对" in user or "核心功能审查表" in user:
            gaps.append({"text": "静态审查缺口", "verification": "runtime"})
        return {
            "json": {
                "purpose": "只读工程观察员",
                "identities": [
                    {"role": "原仓", "head": "abc123", "note": "干净"},
                    {"role": "候选", "head": "未知", "note": ""},
                ],
                "call_chain": "scan 只读 Git，不执行测试",
                "invariants": ["静态审查不等于当前运行通过"],
                "static_gaps": gaps,
                "do_not_claim": ["测试通过", "已上线"],
            }
        }

    def stop(self, model: str) -> None:
        self.stopped.append(model)


class BriefingTests(unittest.TestCase):
    def _home(self) -> tuple[Path, Path]:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = init_repo(root / "repo")
        home = root / "home"
        cfg = website_fixture_config(home, repo, extra_root=root)
        home.mkdir(parents=True)
        docs = Path(cfg.briefing_docs_root) / "website"
        docs.mkdir(parents=True)
        (docs / "00-项目说明.md").write_text(SAMPLE_DOC, encoding="utf-8")
        (docs / "原始文档").mkdir()
        (docs / "原始文档" / "secret.md").write_text(
            "ghp_shouldneverberead0123456789abcd", encoding="utf-8"
        )
        save_config(cfg)
        return root, home

    def test_path_jail_and_exclusions(self) -> None:
        root, home = self._home()
        cfg = load_config(home)
        source, status = load_source(cfg, "website")
        self.assertEqual(status, "ok")
        self.assertIsNotNone(source)
        assert source is not None
        self.assertNotIn("ghp_", source.text)
        self.assertNotIn("原始文档", source.path.parts)

        _, missing = load_source(cfg, "no-such")
        self.assertEqual(missing, "excluded")

        cfg.briefing_docs_root = "relative/docs"
        _, invalid = load_source(cfg, "website")
        self.assertEqual(invalid, "invalid_path")

        cfg = load_config(home)
        cfg.projects.append(
            ProjectSpec(
                project_id="theaiapp-service",
                display_name="company",
                approved_root="/Users/xiuqiu/WorkSpace/theaiapp-service",
                worktrees=[
                    WorktreeSpec(
                        worktree_id="theaiapp-service-main",
                        path="/Users/xiuqiu/WorkSpace/theaiapp-service",
                    )
                ],
            )
        )
        _, excluded = load_source(cfg, "theaiapp-service")
        self.assertEqual(excluded, "excluded")

        cfg = load_config(home)
        outside = root / "outside.md"
        outside.write_text("escaped", encoding="utf-8")
        target = Path(cfg.briefing_docs_root) / "website" / "00-项目说明.md"
        target.unlink()
        target.symlink_to(outside)
        _, denied = load_source(cfg, "website")
        self.assertEqual(denied, "denied")

    def test_split_chunks_keeps_table_and_trailing_bounds(self) -> None:
        chunks = split_chunks(SAMPLE_DOC)
        kinds = [c.kind for c in chunks]
        self.assertIn("identity", kinds)
        self.assertIn("features", kinds)
        self.assertIn("trailing", kinds)
        table = next(c for c in chunks if c.kind == "features")
        self.assertIn("WEB-C01", table.text)
        self.assertIn("仅静态核对", table.text)
        trail = next(c for c in chunks if c.kind == "trailing")
        self.assertIn("未部署", trail.text)

        rows = "\n".join(
            f"| 功能{i} | 入口 | 已实现 | 仅静态核对 | 依据 | 缺口{i} | 建议 |" for i in range(30)
        )
        huge = (
            "# x\n\n用途。\n\n## 核心功能审查表\n\n"
            "| 核心功能与使用场景 | 入口与关键流程 | 实现状态 | 验证状态 | 源码/测试依据 | 问题与边界 | 后续验证建议 |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            f"{rows}\n"
        )
        many = split_chunks(huge)
        feature = [c for c in many if c.kind == "features"]
        self.assertGreaterEqual(len(feature), 2)
        for chunk in feature:
            data_rows = [ln for ln in chunk.text.splitlines() if ln.startswith("| 功能")]
            self.assertLessEqual(len(data_rows), 12)

    def test_gaps_are_static_only_and_secrets_redacted(self) -> None:
        payload = coerce_understanding(
            {
                "purpose": "网关",
                "identities": [{"role": "原仓", "head": "a", "note": ""}],
                "call_chain": "admit",
                "invariants": [],
                "static_gaps": [{"text": "额度缺口", "verification": "runtime"}],
                "do_not_claim": [],
            }
        )
        self.assertEqual(payload["static_gaps"][0]["verification"], "static_only")
        secret = "token ghp_abcdefghijklmnopqrstuvwxyz012345"
        self.assertNotIn("ghp_", redact_secret_spans(secret))

    def test_ingest_writes_cache_and_sha_invalidation(self) -> None:
        _root, home = self._home()
        cfg = load_config(home)
        ensure_layout(Path(cfg.home))
        client = FakeClient()
        first = ingest_project(cfg, "website", client=client)
        self.assertEqual(first["status"], "ok")
        self.assertTrue(client.calls)
        self.assertTrue(client.stopped)
        cached = load_cached_understanding(cfg, "website")
        self.assertIsNotNone(cached)
        assert cached is not None
        gaps = cached["understanding"]["static_gaps"]
        self.assertTrue(gaps)
        self.assertTrue(all(g["verification"] == "static_only" for g in gaps))
        extra = extra_for_summarize(cfg, "website")
        self.assertIn("static_project_briefing", extra)
        self.assertNotIn("原始文档", extra)
        self.assertNotIn("/elsewhere/README.md", extra)
        self.assertIn("static_only", extra)

        second = ingest_project(cfg, "website", client=client)
        self.assertEqual(second["status"], "unchanged")
        calls_after = len(client.calls)

        doc = Path(cfg.briefing_docs_root) / "website" / "00-项目说明.md"
        doc.write_text(SAMPLE_DOC + "\n追加一行。\n", encoding="utf-8")
        self.assertIsNone(load_cached_understanding(cfg, "website"))
        third = ingest_project(cfg, "website", client=client)
        self.assertEqual(third["status"], "ok")
        self.assertGreater(len(client.calls), calls_after)

    def test_legacy_digest_is_rebound_only_after_explicit_ingest(self):
        from xiaomao.ask import project_briefs
        _root, home = self._home()
        cfg = load_config(home)
        self.assertEqual(ingest_project(cfg, "website", client=FakeClient())["status"], "ok")
        body_path, meta_path = cache_paths(cfg, "website")
        body, meta = json.loads(body_path.read_bytes()), json.loads(meta_path.read_bytes())
        del body["scope_sha256"], meta["scope_sha256"], meta["source_path"]
        body_path.write_text(json.dumps(body))
        meta["body_sha256"] = sha256_bytes(body_path.read_bytes())
        meta_path.write_text(json.dumps(meta))
        self.assertEqual(project_briefs(cfg, "website"), [])
        self.assertIsNotNone(load_cached_understanding(cfg, "website"))
        self.assertEqual(ingest_project(cfg, "website")["status"], "unchanged")
        self.assertEqual(project_briefs(cfg, "website")[0]["purpose"], body["understanding"]["purpose"])

    def test_failed_chunk_preserves_complete_cache_and_is_retried(self) -> None:
        limit = "只能读取已登记的个人项目"
        for error in ("TimeoutError", "invalid_json"):
            with self.subTest(error=error):
                _root, home = self._home()
                cfg = load_config(home)
                self.assertEqual(ingest_project(cfg, "website", client=FakeClient())["status"], "ok")
                paths = cache_paths(cfg, "website")
                previous = [path.read_bytes() for path in paths]
                doc = Path(cfg.briefing_docs_root) / "website" / "00-项目说明.md"
                doc.write_text(
                    f"用途：记录个人项目。\n## 核心功能审查表\n| 限制 |\n| --- |\n| {limit} |\n",
                    encoding="utf-8",
                )

                retry = False

                def generate(*, user, **_kwargs):
                    if limit not in user.partition("本块正文：\n")[2]:
                        return {"json": {"purpose": "记录个人项目"}}
                    if retry:
                        return {"json": {"invariants": [limit]}}
                    if error == "TimeoutError":
                        raise TimeoutError()
                    return {"json": {}}

                client = Mock()
                client.generate_json.side_effect = generate
                first = ingest_project(cfg, "website", client=client)
                self.assertEqual(client.generate_json.call_count, 2)
                kept_previous = [path.read_bytes() for path in paths] == previous

                retry = True
                client.reset_mock()
                second = ingest_project(cfg, "website", client=client)
                cached = load_cached_understanding(cfg, "website")
                self.assertIsNotNone(cached)
                self.assertEqual(
                    (first["status"], kept_previous, second["status"], client.generate_json.call_count,
                     limit in cached["understanding"]["invariants"]),
                    ("degraded", True, "ok", 2, True),
                )
                self.assertEqual(first["errors"], [f"chunk_1:{error}"])
                self.assertIsNone(first["path"])

    def test_cli_ingest_retries_an_old_multichunk_cache_once(self) -> None:
        _root, home = self._home()
        cfg = load_config(home)
        source, status = load_source(cfg, "website")
        self.assertEqual(status, "ok")
        self.assertIsNotNone(source)
        chunk_count = len(split_chunks(source.text))
        self.assertGreater(chunk_count, 1)
        write_cache(cfg, source, {"purpose": "只读工程观察员"}, model="old", chunk_count=chunk_count)
        body_path, meta_path = cache_paths(cfg, "website")
        body, meta = json.loads(body_path.read_bytes()), json.loads(meta_path.read_bytes())
        body.pop("all_chunks_processed", None)
        body_path.write_text(json.dumps(body), encoding="utf-8")
        meta["body_sha256"] = sha256_bytes(body_path.read_bytes())
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        self.assertEqual(load_cached_understanding(cfg, "website")["understanding"]["invariants"], [])

        argv = ["--home", str(home), "ingest-briefing", "--project", "website"]
        client = FakeClient()
        statuses = []
        with patch("xiaomao.cli._briefing_client", return_value=client) as factory:
            for _ in range(2):
                with redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(main(argv), 0)
                statuses.append(json.loads(stdout.getvalue())["results"][0]["status"])
        self.assertEqual((statuses, len(client.calls)), (["ok", "unchanged"], chunk_count))
        factory.assert_called_once()
        cached = load_cached_understanding(cfg, "website")
        self.assertEqual(cached["understanding"]["invariants"], ["静态审查不等于当前运行通过"])

    def test_cli_ingest_preserves_digest_while_inference_is_paused(self) -> None:
        _root, home = self._home()
        cfg = load_config(home)
        self.assertEqual(ingest_project(cfg, "website", client=FakeClient())["status"], "ok")
        paths = cache_paths(cfg, "website")
        previous = [path.read_bytes() for path in paths]
        prefix = ["--home", str(home)]
        ingest = prefix + ["ingest-briefing", "--project", "website"]
        doc = Path(cfg.briefing_docs_root) / "website" / "00-项目说明.md"
        client = FakeClient()

        with redirect_stdout(io.StringIO()), patch("xiaomao.cli._briefing_client", return_value=client) as factory:
            self.assertEqual(main(prefix + ["pause", "infer"]), 0)
            self.assertEqual(main(ingest), 0)
            doc.write_text("用途：记录新版个人项目。\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()) as output:
                status = main(ingest)
            self.assertEqual(len(client.calls), 0)
            factory.assert_not_called()
            self.assertEqual(status, 3)
            self.assertEqual(json.loads(output.getvalue())["results"][0]["errors"], ["infer_paused"])
            self.assertEqual([path.read_bytes() for path in paths], previous)

            self.assertEqual(main(prefix + ["resume", "infer"]), 0)
            self.assertEqual(main(ingest), 0)
            factory.assert_called_once()
            self.assertEqual(len(client.calls), 1)
            self.assertNotEqual([path.read_bytes() for path in paths], previous)

    def test_cli_ingest_stops_requesting_chunks_after_inference_is_paused(self) -> None:
        _root, home = self._home()
        cfg = load_config(home)
        self.assertEqual(ingest_project(cfg, "website", client=FakeClient())["status"], "ok")
        paths = cache_paths(cfg, "website")
        previous = [path.read_bytes() for path in paths]
        doc = Path(cfg.briefing_docs_root) / "website" / "00-项目说明.md"
        updated = SAMPLE_DOC + "\n## 新说明\n新增项目用途和维护边界。\n"
        doc.write_text(updated, encoding="utf-8")
        chunk_count = len(split_chunks(updated))
        self.assertGreater(chunk_count, 1)
        prefix = ["--home", str(home)]
        argv = prefix + ["ingest-briefing", "--project", "website"]
        client = FakeClient()
        generate = client.generate_json

        def pause_after_first_chunk(**kwargs):
            answer = generate(**kwargs)
            if len(client.calls) == 1:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(prefix + ["pause", "infer"]), 0)
            return answer

        with (
            patch("xiaomao.cli._briefing_client", return_value=client),
            patch.object(client, "generate_json", side_effect=pause_after_first_chunk),
            redirect_stdout(io.StringIO()) as output,
        ):
            status = main(argv)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(status, 3)
            self.assertEqual(json.loads(output.getvalue())["results"][0]["errors"], ["infer_paused"])
            self.assertEqual([path.read_bytes() for path in paths], previous)

            self.assertEqual(main(prefix + ["resume", "infer"]), 0)
            self.assertEqual(main(argv), 0)
            self.assertEqual(len(client.calls), chunk_count + 1)
            self.assertNotEqual([path.read_bytes() for path in paths], previous)

    def test_scan_does_not_ingest(self) -> None:
        _root, home = self._home()
        buf = io.StringIO()
        with (
            redirect_stdout(buf),
            patch("xiaomao.briefing.ingest_project") as ingest,
            patch("xiaomao.ollama_runtime.OllamaClient") as model_client,
            patch(
                "xiaomao.ollama_runtime.inspect_running",
                return_value={"reachable": False, "host": "isolated"},
            ),
            patch("xiaomao.ollama_runtime.api_get", return_value=None),
        ):
            self.assertEqual(main(["--home", str(home), "init"]), 0)
            self.assertEqual(main(["--home", str(home), "scan", "--project", "website"]), 0)
            self.assertEqual(main(["--home", str(home), "daily", "--date", "2026-09-23"]), 0)
            ingest.assert_not_called()
            model_client.assert_not_called()

    def test_cli_ingest_uses_client_not_live_docs(self) -> None:
        root, home = self._home()
        client = FakeClient()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--home", str(home), "init"]), 0)
        buf = io.StringIO()
        with (
            redirect_stdout(buf),
            patch("xiaomao.cli._briefing_client", return_value=client),
        ):
            self.assertEqual(main(["--home", str(home), "ingest-briefing", "--project", "website"]), 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["results"][0]["status"], "ok")
        cache = home / "reports" / "briefing" / "website.json"
        self.assertTrue(cache.is_file())
        self.assertTrue(client.calls)
        live = "/Users/xiuqiu/Desktop/总doc/QAI/00-项目说明.md"
        for call in client.calls:
            self.assertNotIn(live, call["user"])
            self.assertNotIn("原始文档", call["user"])
            self.assertNotIn("ghp_", call["user"])
        secret_path = str(root / "docs" / "website" / "原始文档" / "secret.md")
        joined = "\n".join(call["user"] for call in client.calls)
        self.assertNotIn(secret_path, joined)

    def test_missing_cache_does_not_invent_understanding(self) -> None:
        _root, home = self._home()
        cfg = load_config(home)
        extra = extra_for_summarize(cfg, "website")
        self.assertIn("尚未 ingest-briefing", extra)
        self.assertNotIn("static_project_briefing", extra)
