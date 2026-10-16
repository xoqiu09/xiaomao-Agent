"""Annotated multi-repository demo. The model responses are fixtures, not AI quality evidence."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.helpers import git, init_repo, website_fixture_config
from xiaomao.collect import scan_authorized
from xiaomao.config import ProjectSpec, WorktreeSpec, save_config
from xiaomao.daily_jobs import run_daily
from xiaomao.feature_context import save_catalog
from xiaomao.store import open_db

EXPECTED = {
    "projects": 3, "trees": 5, "commits": 1, "ongoing_trees": 2,
    "model_projects": ["website", "notes"],
    "must_include": ["每日工作总结", "汇总当天的提交和未提交工作", "草稿保存", "将草稿写入文本文件",
                     "已提交", "尚未提交", "本窗口没有新的推进证据"],
    "must_exclude": ["report.py", "draft.py", "README.md", "AFTER_CUTOFF"],
}


def generate(root):
    app, notes, learning = [init_repo(root / name) for name in ("app", "notes", "learning")]
    (app / "report.py").write_text("def summarize(commits, dirty):\n    return dirty\n")
    (notes / "draft.py").write_text("def save_draft(path, text):\n    pass\n")
    for repo in (app, notes):
        git(repo, "add", ".")
        git(repo, "commit", "-m", "Baseline")
    git(app, "remote", "add", "origin", "https://github.com/xoqiu09/functional-fixture.git")
    linked, copy = root / "development", root / "copy"
    git(app, "worktree", "add", "--detach", str(linked))
    git(root, "clone", str(app), str(copy))
    git(copy, "remote", "set-url", "origin", "https://github.com/xoqiu09/functional-fixture.git")
    cfg = website_fixture_config(root / "home", app)
    cfg.projects[0].display_name = "日报工具"
    cfg.projects[0].worktree_policy = "all"
    cfg.projects[0].worktrees.append(WorktreeSpec("copy", str(copy)))
    cfg.projects.extend([
        ProjectSpec("notes", "笔记工具", str(notes), [WorktreeSpec("notes-main", str(notes))]),
        ProjectSpec("learning", "学习笔记", str(learning), [WorktreeSpec("learning-main", str(learning))]),
    ])
    save_config(cfg)
    (learning / "README.md").write_text("昨天留下的整理草稿。\n")
    db = Path(cfg.home) / "xiaomao.sqlite"
    with open_db(db) as conn:
        for pid, name, paths in (("website", "每日工作总结", ["report.py"]),
                                 ("notes", "草稿保存", ["draft.py"]),
                                 ("learning", "知识整理", ["README.md"])):
            save_catalog(conn, cfg, pid, {"purpose": name, "features": [
                {"id": pid, "name": name, "paths": paths, "priority": 3 if pid == "website" else 2}]},
                at="2026-10-01T12:00:00+00:00")
        def scan(at):
            with patch("xiaomao.collect.utc_now", return_value=at):
                scan_authorized(conn, cfg)
        scan("2026-10-01T13:00:00+00:00")
        (linked / "report.py").write_text("def summarize(commits, dirty):\n    return {'committed': commits, 'ongoing': dirty}\n")
        scan("2026-10-02T02:00:00+00:00")
        git(linked, "add", ".")
        with patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-10-02T03:00:00+00:00"}):
            git(linked, "commit", "-m", "Include committed work in summary")
        scan("2026-10-02T03:05:00+00:00")
        # Transfer only inside the synthetic local fixture; the collector never fetches.
        git(copy, "fetch", str(linked), "HEAD")
        git(copy, "merge", "--ff-only", "FETCH_HEAD")
        (notes / "draft.py").write_text("def save_draft(path, text):\n    path.write_text(text, encoding='utf-8')\n")
        scan("2026-10-02T04:00:00+00:00")
        (notes / "draft.py").write_text("AFTER_CUTOFF\n")
        scan("2026-10-02T13:30:00+00:00")
    calls = []
    class ModelFixture:
        def generate_json(self, **kwargs):
            packet = json.loads(kwargs["user"])
            pid = packet["project_id"]
            calls.append(pid)
            units = [u for u in packet["units"] if u["kind"] == "code"]
            name, before, after, impact = (
                ("每日工作总结", "只返回未提交的工作。", "汇总当天的提交和未提交工作。", "提交后的成果可以与进行中的工作一起呈现。")
                if pid == "website" else
                ("草稿保存", "保存入口尚未写入内容。", "补充了将草稿写入文本文件的逻辑。", "代码仍在工作区，等待提交和运行验证。"))
            support = [{"unit_id": u["unit_id"], "side": side, "quote": u[side]}
                       for u in units for side in ("before", "after") if len(u[side].strip()) >= 4]
            return {"json": {"records": [{"feature_name": name, "change_type": "behavior", "before": before,
                "after": after, "impact": impact, "unit_ids": [u["unit_id"] for u in units], "support": support}]}}
        def stop(self, model):
            raise AssertionError("No real process control")
    now = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
    with patch("xiaomao.collect.utc_now", return_value=now.isoformat()):
        dest = run_daily(cfg, date="2026-10-02", with_model=True, now=now, client_factory=lambda _: ModelFixture())[0]
    with open_db(db) as conn:
        bundle = json.loads(conn.execute("SELECT bundle_json FROM daily_windows").fetchone()[0])
    body = dest.read_text()
    detail = (Path(cfg.home) / "reports/daily-evidence/2026-10-02.txt").read_text()
    menu = (Path(cfg.home) / "reports/briefing/menu-2026-10-02.txt").read_text()
    actual = {"projects": len(bundle["projects"]), "trees": sum(len(p["trees"]) for p in bundle["projects"]),
              "commits": sum(len(p["commits"]) for p in bundle["projects"]),
              "ongoing_trees": sum(len(p["ongoing"]) for p in bundle["projects"]), "model_projects": calls}
    for key, value in actual.items():
        assert value == EXPECTED[key], (key, value, EXPECTED[key])
    for text in EXPECTED["must_include"]:
        assert text in body, text
    for text in EXPECTED["must_exclude"]:
        assert text not in body, text
    assert "AFTER_CUTOFF" not in json.dumps(bundle)
    assert all("功能逻辑" not in r["feature_name"] for rows in bundle["functional_records"].values() for r in rows)
    assert "report.py" in detail and "draft.py" in detail
    assert "汇总当天的提交和未提交工作" in menu and "将草稿写入文本文件" in menu
    return {"body": body, "detail": detail, "menu": menu, "bundle": bundle,
            "verdict": {"status": "PASS", "expected": EXPECTED, "actual": actual,
                        "model": "fixture", "real_model_quality": "NOT_RUN"}}


if __name__ == "__main__":
    import argparse
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="xiaomao-functional-scenario-") as tmp:
        result = generate(Path(tmp))
    args.output.mkdir(parents=True, exist_ok=True)
    for key in ("body", "detail", "menu"):
        (args.output / f"sample-{key}.txt").write_text("隔离验收样例 · 模型替身输出（非真实开发日报）\n\n" + result[key])
    for key in ("bundle", "verdict"):
        (args.output / f"sample-{key}.json").write_text(json.dumps(result[key], ensure_ascii=False, indent=2))
    print(json.dumps(result["verdict"], ensure_ascii=False))
