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


def render_daily(cfg: AppConfig, conn, *, date: str, model_note: str | None = None) -> str:
    lines: list[str] = []
    lines.append(f"小猫日报 {date}")
    lines.append("=" * (6 + len(date)))
    lines.append(f"时区：{cfg.timezone}")
    lines.append(f"生成时间（UTC）：{utc_now()}")
    lines.append("证据栏由程序从 SQLite 渲染，不经模型改写。")
    lines.append("只报告授权观察范围内磁盘可见状态；无变化不等于没有工作。")
    lines.append("")

    any_change = False
    review_candidate: str | None = None
    next_step: str | None = None
    verified: list[str] = []
    unverified = [
        "测试：unknown（本版本不执行业务仓测试，也未接入授权报告）",
        "部署：unknown（本版本无远程连接器）",
    ]
    unknown = [
        "当前版本测试是否通过：unknown",
        "是否已部署 / 已验收：unknown",
        "两次采集之间改了又撤销、或未保存到磁盘的编辑：不可见",
    ]

    for project in cfg.projects:
        if project_exclusion_reason(project):
            continue
        rows = authorized_rows(conn, project)
        last_run = last_scan_success(conn, project.project_id)
        lines.append(f"项目：{project.display_name}（{project.project_id}）")
        lines.append("-" * 24)
        if last_run and last_run["finished_at"]:
            lines.append(f"最近一次扫描成功：{last_run['finished_at']}")
            verified.append(
                f"{project.project_id} 最近一次扫描成功 {last_run['finished_at']} "
                f"（inserted={last_run['inserted']} unchanged={last_run['unchanged']}）"
            )
            gap = gap_seconds_since(last_run["finished_at"])
            if gap is not None and gap > GAP_WARN_AFTER_S:
                lines.append(
                    f"采集缺口约 {gap} 秒：可能含睡眠/关机/登出或其他未采集窗口。"
                    "缺口内未保存到磁盘的编辑不可见，不编造。"
                )
                unknown.append(f"{project.project_id} 采集缺口约 {gap} 秒内的磁盘外编辑")
        else:
            lines.append("最近一次扫描成功：尚无 scan_runs 记录")
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
                unknown.append(f"{row['worktree_id']} 尚未采集")
                continue
            lines.append(f"  指纹首次采集时间：{row['observed_at_utc']}")
            lines.append(f"  分支 / HEAD：{_branch_label(row)} / {_short(row['head_oid'])}")
            lines.append(f"  采集状态：{row['collection_status']}")
            verified.append(
                f"{row['worktree_id']} HEAD {_short(row['head_oid'])} / {_branch_label(row)} / {row['collection_status']}"
            )
            if _facts_changed(row):
                any_change = True
                change = (
                    f"staged {row['staged_count']} / unstaged {row['unstaged_count']} / untracked {row['untracked_count']}"
                )
                lines.append(f"  变化：{change}")
                if review_candidate is None:
                    facts = json.loads(row["facts_json"] or "{}")
                    sample = None
                    for key in ("unstaged", "staged", "untracked"):
                        items = facts.get(key) or []
                        if items:
                            sample = f"{key}:{items[0]}"
                            break
                    review_candidate = (
                        f"{row['worktree_id']} 有未提交磁盘变化（{change}"
                        + (f"，例如 {sample}" if sample else "")
                        + "）。不要把 dirty 解释成已部署。"
                    )
                    next_step = (
                        f"复核 {row['worktree_id']} 的未提交路径（{change}）。"
                        "测试与部署仍 unknown，没有下一步上线依据。"
                    )
            else:
                lines.append("  变化：相对上次指纹，工作区 clean（仅磁盘可见状态）")
            lines.append("  测试：unknown（本版本不执行业务仓测试，也未接入授权报告）")
            lines.append("  部署：unknown（本版本无远程连接器）")
        idle = [r for r in rows if not r["scan_enabled"]]
        if idle:
            names = ", ".join(r["worktree_id"] for r in idle)
            lines.append("已登记未扫描：" + names)
            unverified.append(f"未扫描工作树（仅登记）：{names}")
        lines.append("")

    lines.append("今日观察")
    lines.append("--------")
    if any_change:
        lines.append("授权观察范围内有磁盘可见变化（见上方工作树变化栏）。")
    else:
        lines.append("授权观察范围内无新变化")
    lines.append("")
    lines.append("已核实")
    lines.append("------")
    if verified:
        for item in verified:
            lines.append(f"- {item}")
    else:
        lines.append("- 尚无已核实的采集事实")
    lines.append("")
    lines.append("未核实")
    lines.append("------")
    for item in unverified:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("未知")
    lines.append("----")
    for item in unknown:
        lines.append(f"- {item}")
    lines.append("")
    if review_candidate:
        lines.append("复核候选")
        lines.append("--------")
        lines.append(f"- {review_candidate}")
        lines.append("")
    if next_step:
        lines.append("下一步（有证据）")
        lines.append("--------------")
        lines.append(f"- {next_step}")
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
        lines.append("- 授权观察范围内无新变化。不要编造今日成果，也不要把无变化写成没有工作。")
    lines.append("- 没有测试/部署证据时，状态保持 unknown。")
    return "\n".join(lines) + "\n"


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
    dest = daily_path(cfg, date)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = render_daily(cfg, conn, date=date, model_note=model_note)
    dest.write_text(text, encoding="utf-8")
    from xiaomao.report_access import bind_report

    bind_report(dest, text, cfg, "daily")
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
