"""Inventory inspection and explicit daily settings changes."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from xiaomao.config import load_config, save_config
from xiaomao.inventory import discover, excluded
from xiaomao.paths import layout
from xiaomao.scope import project_exclusion_reason


def projects_command(ns, home: Path) -> int:
    cfg = load_config(home)
    if ns.action == "discover":
        payload = {"repositories": discover(cfg, ns.root),
                   "note": "candidate 仅读取 Git 身份元数据；未纳入代码采集。"}
    elif ns.action == "coverage":
        from xiaomao.daily import build_bundle
        db = layout(home)["db"]
        if not db.exists():
            payload = {"coverage": "unknown", "note": "尚无扫描记录"}
        else:
            conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                bundle = build_bundle(cfg, conn, date=ns.date or datetime.now(ZoneInfo(cfg.timezone)).date().isoformat())
                payload = {k: bundle[k] for k in ("window", "coverage", "candidates", "inventory_errors")}
                payload["projects"] = [{k: p[k] for k in ("project_id", "repo_id", "trees", "gaps", "limitations")}
                                       for p in bundle["projects"]]
            except sqlite3.OperationalError:
                payload = {"coverage": "unknown", "note": "旧数据库尚未开始新版采集"}
            finally:
                conn.close()
    else:
        if ns.root is not None:
            roots = [str(Path(p).expanduser().resolve()) for p in ns.root]
            for root in roots:
                if not Path(root).is_dir() or excluded(root):
                    raise ValueError("个人目录不可用或被排除")
            cfg.personal_roots = list(dict.fromkeys(roots))
        if ns.worktrees:
            if not ns.project:
                raise ValueError("--worktrees 必须指定 --project")
            project = cfg.project(ns.project)
            if project_exclusion_reason(project):
                raise ValueError("项目在排除范围")
            project.worktree_policy = ns.worktrees
            if ns.worktrees == "registered":
                project.branch_prefixes = []
        if ns.time:
            if len(ns.time) != 5:
                raise ValueError("日报时间应为 HH:MM")
            time.fromisoformat(ns.time)
            cfg.daily_time = ns.time
        if ns.timezone:
            ZoneInfo(ns.timezone)
            cfg.timezone = ns.timezone
        if ns.notify:
            cfg.daily_notify = ns.notify == "on"
        save_config(cfg)
        payload = {"config": str(layout(home)["config"]), "personal_roots": cfg.personal_roots,
                   "daily_time": cfg.daily_time, "timezone": cfg.timezone,
                   "daily_notify": cfg.daily_notify,
                   "note": "设置已保存；调度器和正式版本切换需单独发布。"}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0
