from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from xiaomao.config import AppConfig, ProjectSpec
from xiaomao.store import latest_by_project


def _short(oid: str | None) -> str:
    if not oid:
        return "—"
    return oid[:12]


def _branch_label(row) -> str:
    if row["observation_id"] is None:
        return "尚未扫描"
    if row["is_unborn"]:
        ref = row["branch_ref"] or "HEAD"
        return f"{ref} (unborn)"
    if row["is_detached"]:
        return f"detached {_short(row['head_oid'])}"
    return row["branch_ref"] or "HEAD"


def render_project_status(cfg: AppConfig, project: ProjectSpec, rows) -> str:
    lines: list[str] = []
    lines.append(f"项目：{project.display_name}")
    lines.append(f"project_id：{project.project_id}")
    lines.append(f"授权根：{project.approved_root}")
    lines.append(f"时区：{cfg.timezone}")
    lines.append("")
    lines.append("事实")
    lines.append("----")

    any_scan = False
    for row in rows:
        scan_on = bool(row["scan_enabled"])
        lines.append("")
        lines.append(f"工作树：{row['worktree_id']}")
        lines.append(f"  路径：{row['canonical_path']}")
        if not scan_on:
            lines.append("  策略：已登记，本阶段不扫描")
            if row["notes"]:
                lines.append(f"  备注：{row['notes']}")
            continue
        any_scan = True
        if row["observation_id"] is None:
            lines.append("  采集：尚未执行")
            continue
        lines.append(f"  采集时间：{row['observed_at_utc']}")
        lines.append(f"  分支 / HEAD：{_branch_label(row)} / {_short(row['head_oid'])}")
        lines.append(f"  采集状态：{row['collection_status']}")
        dirty = int(row["staged_count"] or 0) + int(row["unstaged_count"] or 0) + int(row["untracked_count"] or 0)
        if dirty == 0:
            lines.append("  当前变化：工作区 clean（仅就本次采集可见的磁盘状态）")
        else:
            lines.append(
                f"  当前变化：staged {row['staged_count']} / unstaged {row['unstaged_count']} / untracked {row['untracked_count']}"
            )
            facts = json.loads(row["facts_json"] or "{}")
            for label, key in (("staged", "staged"), ("unstaged", "unstaged"), ("untracked", "untracked")):
                items = facts.get(key) or []
                if not items:
                    continue
                shown = items[:20]
                for p in shown:
                    lines.append(f"    - {label}: {p}")
                if len(items) > 20:
                    lines.append(f"    - …另有 {len(items) - 20} 个 {label} 路径")
            denied = facts.get("denied_paths") or []
            if denied:
                lines.append(f"  敏感路径（只记名，不读内容）：{len(denied)}")

    lines.append("")
    lines.append("测试")
    lines.append("----")
    lines.append("状态：unknown")
    lines.append("说明：第一版不执行测试，也尚未接入授权的测试报告。")
    lines.append("")
    lines.append("部署")
    lines.append("----")
    lines.append("状态：unknown")
    lines.append("说明：第一版没有远程连接器。")
    lines.append("")
    lines.append("模型解读")
    lines.append("--------")
    lines.append("本报告由规则程序生成，没有调用本地模型。")
    lines.append("")
    lines.append("建议")
    lines.append("----")
    if not any_scan:
        lines.append("- 至少启用一棵工作树的 scan=true 后再采集。")
    else:
        lines.append("- 没有测试/部署证据时，不要把 clean 工作区当成已上线。")
        lines.append("- 新发现的工作树只列为候选，不会自动获得读取授权。")
    lines.append("")
    lines.append("未知项")
    lines.append("------")
    lines.append("- 当前版本的测试是否通过：unknown")
    lines.append("- 是否已部署 / 已验收：unknown")
    lines.append("- 两次采集之间改了又撤销、或未保存到磁盘的编辑：不可见")
    return "\n".join(lines) + "\n"


def write_project_report(cfg: AppConfig, project: ProjectSpec, rows, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = render_project_status(cfg, project, rows)
    dest.write_text(text, encoding="utf-8")
    return dest


def default_report_path(cfg: AppConfig, project_id: str) -> Path:
    from xiaomao.paths import layout

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return layout(Path(cfg.home))["reports_projects"] / f"{project_id}-{stamp}.txt"


def status_text(conn, cfg: AppConfig, project_id: str) -> str:
    from xiaomao.store import infer_paused, last_scan_success, scan_paused

    project = cfg.project(project_id)
    rows = latest_by_project(conn, project_id)
    last = last_scan_success(conn, project_id)
    last_s = last["finished_at"] if last and last["finished_at"] else "尚无 scan_runs 成功记录"
    header = [
        f"最近一次扫描成功：{last_s}",
        f"推理暂停：{'是' if infer_paused(conn) else '否'}",
        f"扫描暂停：{'是' if scan_paused(conn) else '否'}",
        "",
    ]
    return "\n".join(header) + render_project_status(cfg, project, rows)
