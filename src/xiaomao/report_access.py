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
    projects = [cfg.project(project_id)] if project_id else cfg.projects
    return sorted(scope_identity(p) for p in projects if not project_exclusion_reason(p))


def bind_report(path: Path, text: str, cfg: AppConfig, kind: str, project_id: str | None = None) -> None:
    meta = {"schema": "xiaomao-report-scope-v1", "kind": kind,
            "scope": scope_set(cfg, project_id), "sha256": hashlib.sha256(text.encode()).hexdigest()}
    # A concurrent reader can see a mismatched pair, which fails closed.
    path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")


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
