"""Rule-based daily log and handoff. Git facts are rendered by the program."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from xiaomao.config import AppConfig, ProjectSpec
from xiaomao.ops import GAP_WARN_AFTER_S, gap_seconds_since
from xiaomao.paths import layout
from xiaomao.render import _branch_label, _short, render_project_status, authorized_rows
from xiaomao.scope import project_exclusion_reason
from xiaomao.store import last_scan_success, latest_by_project, utc_now


PROMPT_VERSION = "v0.1-rule"


def _tz(cfg: AppConfig):
    try:
        return ZoneInfo(cfg.timezone)
    except Exception:
        return ZoneInfo("Asia/Taipei")


def local_today(cfg: AppConfig) -> str:
    now = datetime.now(_tz(cfg))
    return now.strftime("%Y-%m-%d")


def _facts_changed(row) -> bool:
    if row["observation_id"] is None:
        return False
    return (
        int(row["staged_count"] or 0)
        + int(row["unstaged_count"] or 0)
        + int(row["untracked_count"] or 0)
    ) > 0


def prefix_tree_rows(conn, project) -> list:
    """Latest observation of each live branch-prefix tree. Supplementary only:
    these trees are not part of the registered scope or handoff coverage."""
    from xiaomao.collect import PREFIX_WORKTREE_NOTE

    if not project.branch_prefixes:
        return []
    return list(
        conn.execute(
            """
            SELECT w.worktree_id, w.canonical_path, o.observation_id, o.observed_at_utc,
                   o.head_oid, o.branch_ref, o.collection_status,
                   o.staged_count, o.unstaged_count, o.untracked_count,
                   json_extract(o.facts_json, '$.last_commit_at') AS last_commit_at
            FROM worktrees w
            LEFT JOIN observations o ON o.observation_id = (
              SELECT observation_id FROM observations
              WHERE worktree_id = w.worktree_id AND project_id = w.project_id
              ORDER BY observed_at_utc DESC, rowid DESC LIMIT 1
            )
            WHERE w.project_id = ? AND w.notes = ? AND w.scan_enabled = 1
            ORDER BY o.branch_ref
            """,
            (project.project_id, PREFIX_WORKTREE_NOTE),
        )
    )


def render_daily(cfg: AppConfig, conn, *, date: str, model_note: str | None = None) -> str:
    from xiaomao.daily import build_bundle, render_bundle
    body = render_bundle(build_bundle(cfg, conn, date=date))
    if model_note:
        body += "\n附加解读：\n" + model_note + "\n"
    return body


def daily_path(cfg: AppConfig, date: str) -> Path:
    return layout(Path(cfg.home))["reports_daily"] / f"{date}.txt"


def daily_model_path(cfg: AppConfig, date: str) -> Path:
    return layout(Path(cfg.home))["reports_daily"] / f"{date}.model.txt"


def write_daily(
    cfg: AppConfig,
    conn,
    *,
    date: str | None = None,
    model_note: str | None = None,
    model_ok: bool = False,
) -> Path:
    date = date or local_today(cfg)
    from xiaomao.daily import build_bundle, render_bundle
    from xiaomao.daily_jobs import preserve_report
    from xiaomao.menu_briefing import _atomic_write
    bundle = build_bundle(cfg, conn, date=date)
    dest = daily_path(cfg, date)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = render_bundle(bundle)
    if model_note:
        text += "\n附加解读：\n" + model_note + "\n"
    preserve_report(dest, text)
    _atomic_write(dest, text)
    from xiaomao.report_access import bind_report

    bind_report(dest, text, cfg, "daily", daily_bundle=bundle)
    if model_ok and model_note:
        daily_model_path(cfg, date).write_text(text, encoding="utf-8")
        bind_report(daily_model_path(cfg, date), text, cfg, "daily")
    return dest


def render_handoff(
    cfg: AppConfig,
    project: ProjectSpec,
    rows,
    *,
    model_note: str | None = None,
    extra_unknown: list[str] | None = None,
    generated_at: str | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"小猫 Handoff — {project.display_name}")
    lines.append("=" * (14 + len(project.display_name)))
    lines.append(f"project_id：{project.project_id}")
    lines.append(f"授权根：{project.approved_root}")
    lines.append(f"时区：{cfg.timezone}")
    lines.append(f"生成时间（UTC）：{generated_at or utc_now()}")
    lines.append("")
    lines.append("约束")
    lines.append("----")
    lines.append("- 业务仓只读：不 fetch、不测、不改 index / hooks / 工作区。")
    lines.append("- 新工作树只列为候选，不自动授权。")
    lines.append("- 无证据 = unknown。旧测试报告不能升级为当前通过。")
    lines.append("- 模型没有 shell，不能写 Git / 数据库。")
    lines.append("")
    lines.append(render_project_status(cfg, project, rows).rstrip())
    if model_note:
        # render_project_status already includes a 模型解读 section; append the
        # actual model (or degradation) note without letting it rewrite facts.
        lines.append("")
        lines.append("模型附加解读（校验后）")
        lines.append("----------------")
        lines.append(model_note)
    if extra_unknown:
        lines.append("")
        lines.append("额外未知项")
        lines.append("----------")
        for item in extra_unknown:
            lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def handoff_path(cfg: AppConfig, project_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", project_id):
        raise ValueError("invalid project ID")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return layout(Path(cfg.home))["reports_handoff"] / f"{project_id}-{stamp}.txt"


def write_handoff(
    cfg: AppConfig,
    conn,
    project_id: str,
    *,
    model_note: str | None = None,
) -> Path:
    from xiaomao.handoff_view import metadata_for
    from xiaomao.scope import project_exclusion_reason

    project = cfg.project(project_id)
    if reason := project_exclusion_reason(project):
        raise ValueError(reason)
    rows = authorized_rows(conn, project)
    dest = handoff_path(cfg, project_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    generated_at = utc_now()
    body = render_handoff(cfg, project, rows, model_note=model_note, generated_at=generated_at)
    meta = metadata_for(conn, project, body, generated_at)
    # A reader encountering only one half of the pair reports unverified. It
    # never falls back to an older successful handoff.
    for path, text in ((dest, body), (dest.with_suffix(".json"), json.dumps(meta, ensure_ascii=False, indent=2) + "\n")):
        tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    return dest


def facts_payload(cfg: AppConfig, conn, project_id: str) -> dict[str, Any]:
    """Program-generated facts. The model must not rewrite these fields."""
    project = cfg.project(project_id)
    if reason := project_exclusion_reason(project):
        raise ValueError(reason)
    rows = authorized_rows(conn, project)
    worktrees = []
    evidence_ids: list[str] = []
    for row in rows:
        facts = json.loads(row["facts_json"] or "{}") if row["observation_id"] else {}
        obs_id = row["observation_id"]
        wt_eid = f"worktree:{row['worktree_id']}"
        evidence_ids.append(wt_eid)
        if obs_id:
            evidence_ids.append(f"observation:{obs_id}")
            for ev in conn.execute(
                "SELECT evidence_id FROM evidence WHERE observation_id = ?",
                (obs_id,),
            ):
                evidence_ids.append(ev["evidence_id"])
        worktrees.append(
            {
                "worktree_id": row["worktree_id"],
                "evidence_id": wt_eid,
                "path": row["canonical_path"],
                "scan_enabled": bool(row["scan_enabled"]),
                "observation_id": obs_id,
                "observed_at_utc": row["observed_at_utc"],
                "head_oid": row["head_oid"],
                "branch_ref": row["branch_ref"],
                "collection_status": row["collection_status"],
                "is_unborn": bool(row["is_unborn"]) if row["observation_id"] else None,
                "is_detached": bool(row["is_detached"]) if row["observation_id"] else None,
                "staged_count": row["staged_count"],
                "unstaged_count": row["unstaged_count"],
                "untracked_count": row["untracked_count"],
                "test_status": facts.get("test_status", "unknown"),
                "deploy_status": facts.get("deploy_status", "unknown"),
                "denied_paths": facts.get("denied_paths") or [],
                "last_commit_at": facts.get("last_commit_at"),
                "recent_subjects": list(facts.get("recent_subjects") or []),
                "module_digest": {
                    "new_modules": list((facts.get("module_digest") or {}).get("new_modules") or []),
                    "gone_modules": list((facts.get("module_digest") or {}).get("gone_modules") or []),
                    "changed_modules": list((facts.get("module_digest") or {}).get("changed_modules") or []),
                },
            }
        )
    return {
        "project_id": project.project_id,
        "display_name": project.display_name,
        "approved_root": project.approved_root,
        "timezone": cfg.timezone,
        "generated_at": utc_now(),
        "prompt_version": PROMPT_VERSION,
        "worktrees": worktrees,
        "evidence_ids": evidence_ids,
        "test_status": "unknown",
        "deploy_status": "unknown",
    }
