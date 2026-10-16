"""Synthetic, annotated acceptance scenario; never uses user repositories."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.helpers import git, init_repo, website_fixture_config
from xiaomao.collect import scan_authorized
from xiaomao.config import ProjectSpec, WorktreeSpec
from xiaomao.daily import build_bundle, render_bundle
from xiaomao.store import open_db

# Written before collection: assertions are independent of rendered prose.
EXPECTED = {
    "projects": 3, "trees": 5, "new_commits": 1, "ongoing_trees": 2,
    "changed_projects": ["website", "notes"], "candidates": 1,
    "must_include": ["仍在推进的功能", "各项目功能变化", "待确认与采集缺口"],
    "detail_must_include": ["新增重试恢复", "draft.py", "其他工作树", "合并叙述"],
    "must_exclude": ["AFTER_CUTOFF_ONLY", "UNKNOWN_SOURCE_MUST_NOT_BE_READ"],
}


def generate(root: Path) -> tuple[dict, str, dict]:
    repo = init_repo(root / "app")
    git(repo, "remote", "add", "origin", "https://github.com/xoqiu09/scenario.git")
    linked = root / "development"
    git(repo, "worktree", "add", "--detach", str(linked))
    copy = root / "copy"
    git(root, "clone", str(repo), str(copy))
    git(copy, "remote", "set-url", "origin", "https://github.com/xoqiu09/scenario.git")
    notes, legacy = init_repo(root / "notes"), init_repo(root / "legacy")
    unknown = init_repo(root / "unknown")
    (unknown / "private.py").write_text("UNKNOWN_SOURCE_MUST_NOT_BE_READ\n")
    cfg = website_fixture_config(root / "home", repo)
    cfg.personal_roots = [str(root)]
    cfg.projects[0].worktree_policy = "all"
    cfg.projects[0].worktrees.append(WorktreeSpec("copy", str(copy)))
    cfg.projects.extend([
        ProjectSpec("notes", "笔记工具", str(notes), [WorktreeSpec("notes-main", str(notes))]),
        ProjectSpec("legacy", "长期开发项", str(legacy), [WorktreeSpec("legacy-main", str(legacy))]),
    ])
    (legacy / "README.md").write_text("昨日遗留，今天未更改\n")
    with open_db(root / "home/xiaomao.sqlite") as conn:
        def scan(at):
            with patch("xiaomao.collect.utc_now", return_value=at):
                scan_authorized(conn, cfg)
        scan("2026-10-01T13:00:00+00:00")
        (linked / "retry.py").write_text("def retry():\n    return 'recovered'\n")
        scan("2026-10-02T02:00:00+00:00")
        git(linked, "add", "retry.py")
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-10-02T03:00:00+00:00"}):
            git(linked, "commit", "-m", "新增重试恢复")
        scan("2026-10-02T03:05:00+00:00")
        # Same object visible in another checkout must remain one commit.
        git(copy, "fetch", str(linked), "HEAD")
        git(copy, "merge", "--ff-only", "FETCH_HEAD")
        (notes / "draft.py").write_text("draft = 'window version'\n")
        (linked / "idea.py").write_text("temporary = True\n")
        scan("2026-10-02T04:00:00+00:00")
        (linked / "idea.py").unlink()
        scan("2026-10-02T04:05:00+00:00")
        (notes / "draft.py").write_text("AFTER_CUTOFF_ONLY\n")
        scan("2026-10-02T13:30:00+00:00")
        bundle = build_bundle(cfg, conn, date="2026-10-02",
                              now=datetime(2026, 10, 2, 14, tzinfo=timezone.utc))
    body = render_bundle(bundle)
    actual = {
        "projects": len(bundle["projects"]),
        "trees": sum(len(p["trees"]) for p in bundle["projects"]),
        "new_commits": sum(len(p["commits"]) for p in bundle["projects"]),
        "ongoing_trees": sum(len(p["ongoing"]) for p in bundle["projects"]),
        "changed_projects": [p["project_id"] for p in bundle["projects"] if p["changed"]],
        "candidates": len(bundle["candidates"]),
    }
    for key, value in actual.items():
        assert value == EXPECTED[key], (key, value, EXPECTED[key])
    for text in EXPECTED["must_include"]:
        assert text in body, text
    from xiaomao.feature_render import render_details
    detail = render_details(bundle, {})
    assert "xiaomao-test" not in body and "xiaomao-test" not in detail
    assert any(c.get("author") == "xiaomao-test" for p in bundle["projects"] for c in p["commits"])
    for text in EXPECTED["detail_must_include"]:
        assert text in detail, text
    assert "draft.py" not in body
    for text in EXPECTED["must_exclude"]:
        assert text not in json.dumps(bundle, ensure_ascii=False), text
    assert any(e["kind"] == "restored" for p in bundle["projects"] for e in p["activity"])
    assert bundle["coverage"] == "partial"
    return bundle, body, {"expected": EXPECTED, "actual": actual, "status": "PASS"}


if __name__ == "__main__":
    import argparse
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="xiaomao-daily-scenario-") as tmp:
        bundle, body, verdict = generate(Path(tmp))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "sample-daily.txt").write_text(body)
    (args.output / "sample-evidence.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2))
    (args.output / "sample-checks.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2))
    print(json.dumps(verdict, ensure_ascii=False))
