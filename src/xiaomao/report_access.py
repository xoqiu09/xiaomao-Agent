"""Bind daily/status files to the current allowed scope before exposing text.

Old aggregate reports may contain projects that have since been excluded. A
legacy file is retained on disk but cannot bypass the current read boundary.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from xiaomao.config import AppConfig
from xiaomao.handoff_view import _read_file, scope_identity
from xiaomao.scope import project_exclusion_reason


def scope_set(cfg: AppConfig, project_id: str | None = None) -> list[str]:
    if project_id is None and hasattr(cfg, "_daily_scope"):
        return list(cfg._daily_scope)
    projects = [cfg.project(project_id)] if project_id else cfg.projects
    scopes = [scope_identity(p) for p in projects if not project_exclusion_reason(p)]
    settings = {"roots": cfg.personal_roots, "owners": cfg.personal_owners if cfg.personal_roots else [],
                "policies": [(p.project_id, p.worktree_policy, p.branch_prefixes)
                             for p in projects if not project_exclusion_reason(p)],
                "daily_time": cfg.daily_time, "timezone": cfg.timezone}
    if scopes or cfg.personal_roots:
        scopes.append(hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest())
    return sorted(scopes)


def bind_report(path: Path, text: str, cfg: AppConfig, kind: str, project_id: str | None = None,
                *, daily_bundle: dict | None = None) -> None:
    meta = {"schema": "xiaomao-report-scope-v1", "kind": kind,
            "scope": scope_set(cfg, project_id), "sha256": hashlib.sha256(text.encode()).hexdigest()}
    if daily_bundle is not None:
        meta.update(window=daily_bundle["window"], evidence_version=daily_bundle["schema"],
                    evidence_hash=daily_bundle["evidence_hash"], coverage=daily_bundle["coverage"],
                    projects=[{"project_id": p["project_id"], "repo_id": p["repo_id"], "trees": p["trees"]}
                              for p in daily_bundle["projects"]])
    # A concurrent reader can see a mismatched pair, which fails closed.
    from xiaomao.menu_briefing import _atomic_write
    _atomic_write(path.with_suffix(".json"), json.dumps(meta, ensure_ascii=False) + "\n")


def read_bound_report(path: Path, home: Path, cfg: AppConfig, kind: str, project_id: str | None = None) -> str:
    expected = scope_set(cfg, project_id)
    if not expected:
        raise ValueError("没有可读取的授权范围")
    folder_name = {"status": "projects", "briefing": "briefing"}.get(kind, "daily")
    folder = home / "reports" / folder_name
    if folder.resolve() != home.resolve() / "reports" / folder.name:
        raise ValueError("报告目录越出数据 home")
    meta = json.loads(_read_file(path.with_suffix(".json"), folder, 128 * 1024))
    if (not isinstance(meta, dict) or meta.get("schema") != "xiaomao-report-scope-v1"
            or meta.get("kind") != kind or meta.get("scope") != expected):
        raise ValueError("旧报告范围未核实；请从当前个人项目重新生成")
    body = _read_file(path, folder, 2 * 1024 * 1024)
    if hashlib.sha256(body).hexdigest() != meta.get("sha256"):
        raise ValueError("报告来源校验失败")
    return body.decode("utf-8")
