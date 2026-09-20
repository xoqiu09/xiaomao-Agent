"""Rule-based daily log and handoff. Git facts are rendered by the program."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from xiaomao.config import AppConfig, ProjectSpec
from xiaomao.paths import layout
from xiaomao.render import _branch_label, _short, render_project_status
from xiaomao.store import latest_by_project, utc_now


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


def render_daily(cfg: AppConfig, conn, *, date: str, model_note: str | None = None) -> str:
    lines: list[str] = []
    lines.append(f"小猫日报 {date}")
    lines.append("=" * (6 + len(date)))
    lines.append(f"时区：{cfg.timezone}")
    lines.append(f"生成时间（UTC）：{utc_now()}")
    lines.append("证据栏由程序从 SQLite 渲染，不经模型改写。")
    lines.append("")

    any_change = False
    for project in cfg.projects:
        rows = latest_by_project(conn, project.project_id)
        lines.append(f"项目：{project.display_name}（{project.project_id}）")
        lines.append("-" * 24)
        scanned = [r for r in rows if r["scan_enabled"]]
        if not scanned:
            lines.append("本阶段没有启用扫描的工作树。")
            lines.append("")
            continue
        for row in scanned:
            lines.append(f"工作树：{row['worktree_id']}")
            if row["observation_id"] is None:
                lines.append("  事实：尚未采集")
                lines.append("  未知：当前 HEAD / 工作区")
                continue
            lines.append(f"  采集时间：{row['observed_at_utc']}")
            lines.append(f"  分支 / HEAD：{_branch_label(row)} / {_short(row['head_oid'])}")
            lines.append(f"  采集状态：{row['collection_status']}")
            if _facts_changed(row):
                any_change = True
                lines.append(
                    "  变化："
                    f"staged {row['staged_count']} / unstaged {row['unstaged_count']} / untracked {row['untracked_count']}"
                )
            else:
                lines.append("  变化：相对上次指纹，工作区 clean（仅磁盘可见状态）")
            lines.append("  测试：unknown（本版本不执行业务仓测试，也未接入授权报告）")
            lines.append("  部署：unknown（本版本无远程连接器）")
        idle = [r for r in rows if not r["scan_enabled"]]
        if idle:
            lines.append("已登记未扫描：" + ", ".join(r["worktree_id"] for r in idle))
        lines.append("")

    lines.append("模型解读")
    lines.append("--------")
    if model_note:
        lines.append(model_note)
    else:
        lines.append("本报告由规则程序生成，没有调用本地模型。")
    lines.append("")
    lines.append("建议")
    lines.append("----")
    if any_change:
        lines.append("- 有未提交变化。不要把工作区 dirty 或 clean 解释成已部署。")
    else:
        lines.append("- 没有新的磁盘可见变化。不要编造今日成果。")
    lines.append("- 没有测试/部署证据时，状态保持 unknown。")
    lines.append("")
    lines.append("未知项")
    lines.append("------")
    lines.append("- 当前版本测试是否通过：unknown")
    lines.append("- 是否已部署 / 已验收：unknown")
    lines.append("- 两次采集之间改了又撤销、或未保存到磁盘的编辑：不可见")
    return "\n".join(lines) + "\n"


def daily_path(cfg: AppConfig, date: str) -> Path:
    return layout(Path(cfg.home))["reports_daily"] / f"{date}.txt"


def write_daily(cfg: AppConfig, conn, *, date: str | None = None, model_note: str | None = None) -> Path:
    date = date or local_today(cfg)
    dest = daily_path(cfg, date)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_daily(cfg, conn, date=date, model_note=model_note), encoding="utf-8")
    return dest


def render_handoff(
    cfg: AppConfig,
    project: ProjectSpec,
    rows,
    *,
    model_note: str | None = None,
    extra_unknown: list[str] | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"小猫 Handoff — {project.display_name}")
    lines.append("=" * (14 + len(project.display_name)))
    lines.append(f"project_id：{project.project_id}")
    lines.append(f"授权根：{project.approved_root}")
    lines.append(f"时区：{cfg.timezone}")
    lines.append(f"生成时间（UTC）：{utc_now()}")
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
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return layout(Path(cfg.home))["reports_handoff"] / f"{project_id}-{stamp}.txt"


def write_handoff(
    cfg: AppConfig,
    conn,
    project_id: str,
    *,
    model_note: str | None = None,
) -> Path:
    project = cfg.project(project_id)
    rows = latest_by_project(conn, project_id)
    dest = handoff_path(cfg, project_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_handoff(cfg, project, rows, model_note=model_note), encoding="utf-8")
    return dest


def facts_payload(cfg: AppConfig, conn, project_id: str) -> dict[str, Any]:
    """Program-generated facts. The model must not rewrite these fields."""
    project = cfg.project(project_id)
    rows = latest_by_project(conn, project_id)
    worktrees = []
    evidence_ids: list[str] = []
    for row in rows:
        facts = json.loads(row["facts_json"] or "{}") if row["observation_id"] else {}
        obs_id = row["observation_id"]
        if obs_id:
            for ev in conn.execute(
                "SELECT evidence_id FROM evidence WHERE observation_id = ?",
                (obs_id,),
            ):
                evidence_ids.append(ev["evidence_id"])
        worktrees.append(
            {
                "worktree_id": row["worktree_id"],
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
