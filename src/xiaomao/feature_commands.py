"""Explicit local catalog corrections; showing context never invokes a model."""
import json
import sqlite3
from pathlib import Path

from xiaomao.config import load_config
from xiaomao.feature_context import catalog_at, save_catalog
from xiaomao.inventory import scope_key
from xiaomao.paths import layout
from xiaomao.scope import project_exclusion_reason
from xiaomao.store import open_db, utc_now


def features_command(ns, home):
    cfg = load_config(home)
    project = cfg.project(ns.project)
    if project_exclusion_reason(project):
        raise ValueError("项目在排除范围")
    db = layout(home)["db"]
    if ns.action == "set":
        if not ns.file:
            raise ValueError("需要 --file 功能目录 JSON")
        from xiaomao.handoff_view import _read_file
        from xiaomao.lock import ScanLock
        source = Path(ns.file).expanduser().absolute()
        payload = json.loads(_read_file(source, source.parent, 32 * 1024))
        with ScanLock(layout(home)["lock"]), open_db(db) as conn:
            result = save_catalog(conn, cfg, project.project_id, payload)
        result["note"] = "修正从后续采集生效；旧日报背景保留原版本。"
    elif not db.exists():
        result = {"context": None, "note": "尚无采集记录"}
    else:
        with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            try:
                correction = catalog_at(conn, project, utc_now())
                contexts = []
                for row in conn.execute("SELECT state_json FROM daily_trees WHERE scope_key=?", (scope_key(project),)):
                    context = json.loads(row[0]).get("feature_context")
                    if context and context not in contexts:
                        contexts.append(context)
                result = {"correction": correction, "observed_contexts": contexts,
                          "note": "静态项目背景不代表今日成果、测试或发布状态。"}
            except sqlite3.OperationalError:
                result = {"context": None, "note": "旧数据库尚无功能目录记录"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
