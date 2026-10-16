"""One evidence packet for the daily text, model and SwiftBar briefing."""
from __future__ import annotations

import hashlib
import json
from datetime import date as Date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from xiaomao.activity import stamp
from xiaomao.inventory import effective_config, excluded, metadata, scope_key
from xiaomao.ops import GAP_WARN_AFTER_S
from xiaomao.report_access import scope_set
from xiaomao.scope import project_exclusion_reason


def report_window(cfg, date: str) -> tuple[datetime, datetime]:
    cutoff = time.fromisoformat(cfg.daily_time)
    end = datetime.combine(Date.fromisoformat(date), cutoff, ZoneInfo(cfg.timezone))
    start = datetime.combine(end.date() - timedelta(days=1), cutoff, ZoneInfo(cfg.timezone))
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def window_key(cfg, date: str) -> str:
    return f"{cfg.timezone}:{cfg.daily_time}:{date}"


def completed_date(cfg, now: datetime) -> str:
    local = now.astimezone(ZoneInfo(cfg.timezone))
    date = local.date()
    if local.time().replace(tzinfo=None) < time.fromisoformat(cfg.daily_time):
        date -= timedelta(days=1)
    return date.isoformat()


def _file_map(state):
    return {f["path"]: f for f in state.get("files", [])}


def _content_key(row):
    return tuple(row.get(k) for k in ("content_hash", "index_hash", "status", "availability"))


def _event_order(event):
    return event["at"], event.get("sequence", 0)


def _activity(previous, state, row):
    from xiaomao.feature_evidence import observed_delta
    before, after = _file_map(previous), _file_map(state)
    paths = sorted(p for p in before.keys() | after.keys()
                   if _content_key(before.get(p, {})) != _content_key(after.get(p, {})))
    if state.get("gone"):
        kind = "gone"
    elif not paths:
        if previous.get("head") == state.get("head") and previous.get("branch") == state.get("branch"):
            return None
        kind = "head_change"
    elif before and not after:
        kind = "restored" if previous.get("head") == state.get("head") else "resolved"
    else:
        kind = "workspace"
    return {
        "evidence_id": row["sample_id"], "tree_id": row["tree_id"], "at": row["at_utc"],
        "sequence": row["sample_order"],
        "kind": kind, "paths": paths,
        "files": [observed_delta(before.get(p, {}), after[p], p) if p in after
                  else dict(before.get(p) or {"path": p},
                            resolution_kind="restored" if previous.get("head") == state.get("head") else "resolved")
                  for p in paths],
        "head": state.get("head"), "before_head": previous.get("head"),
        "context_version": state.get("feature_context", {}).get("version"),
    }


def _group(project, repo_id):
    return {
        "project_id": project.project_id, "display_name": project.display_name,
        "aliases": [project.project_id], "repo_id": repo_id, "trees": [],
        "commits": [], "activity": [], "ongoing": [], "gaps": [], "limitations": [],
        "evidence_ids": [], "changed": False,
        "feature_contexts": {},
    }


def _coverage(conn, key, start, end, now, group):
    horizon = min(end, now).isoformat()
    checks = list(conn.execute(
        "SELECT * FROM daily_checks WHERE scope_key=? AND at_utc<? ORDER BY at_utc,rowid",
        (key, horizon),
    ))
    before = [r for r in checks if r["at_utc"] < start.isoformat()]
    within = [r for r in checks if r["at_utc"] >= start.isoformat()]
    selected = before[-1:] + within
    if not selected:
        group["gaps"].append("该窗口没有扫描覆盖证据")
        return
    points = [max(start, datetime.fromisoformat(r["at_utc"])) for r in selected]
    points.append(min(end, now))
    if not before and points[0] > start:
        group["gaps"].append("首次接入或缺少窗口起点基线；此前未提交修改不可恢复")
    for a, b in zip(points, points[1:]):
        if (b - a).total_seconds() > GAP_WARN_AFTER_S:
            group["gaps"].append(f"采集缺口：{a.isoformat()} → {b.isoformat()}")
    for row in within:
        for result in json.loads(row["results_json"]):
            if result.get("status") not in {"ok", "unchanged", "gone"}:
                group["gaps"].append(f"{result.get('worktree_id') or 'project'}: 采集 {result.get('status')}")
    for row in selected:
        for result in json.loads(row["results_json"]):
            if result.get("status") in {"ok", "unchanged", "gone"}:
                for tree in group["trees"]:
                    if tree["tree_id"] == result.get("worktree_id"):
                        tree["last_seen"] = row["at_utc"]


def build_bundle(cfg, conn, *, date: str, now: datetime | None = None) -> dict:
    cfg = effective_config(cfg)
    now = now or datetime.now(timezone.utc)
    start, end = report_window(cfg, date)
    groups = {}
    for project in cfg.projects:
        if project_exclusion_reason(project):
            continue
        key = scope_key(project)
        trees = list(conn.execute("SELECT * FROM daily_trees WHERE scope_key=?", (key,)))
        repo_id = getattr(project, "_repo_id", None) or (trees[0]["repo_id"] if trees else f"project:{project.project_id}")
        group = groups.setdefault(repo_id, _group(project, repo_id))
        if project.project_id not in group["aliases"]:
            group["aliases"].append(project.project_id)
        explicit = {w.worktree_id: w for w in project.worktrees}
        eligible_trees = []
        for tree in trees:
            if tree["repo_id"] != repo_id:
                group["gaps"].append(f"{tree['tree_id']}: 登记项目包含不同仓库，需分别登记")
                continue
            if excluded(tree["path"]):
                continue
            wt = explicit.get(tree["tree_id"])
            if wt is not None and (not wt.scan or str(Path(wt.path).resolve()) != tree["path"]):
                continue
            if wt is None and not (project.worktree_policy == "all" or project.branch_prefixes):
                continue
            # A replaced checkout must never lend the old source's contents.
            if Path(tree["path"]).is_dir():
                try:
                    if metadata(tree["path"])["repo_id"] != repo_id:
                        group["gaps"].append(f"{tree['tree_id']}: repository_identity_changed")
                        continue
                except Exception:
                    group["gaps"].append(f"{tree['tree_id']}: repository_identity_unavailable")
                    continue
            eligible_trees.append(tree)
            samples = list(conn.execute(
                "SELECT rowid AS sample_order,* FROM daily_samples WHERE scope_key=? AND tree_id=? AND repo_id=? AND at_utc<? "
                "ORDER BY at_utc,rowid", (key, tree["tree_id"], repo_id, end.isoformat()),
            ))
            if not samples:
                continue
            previous = None
            for row in samples:
                state = json.loads(row["state_json"])
                context = state.get("feature_context")
                if context:
                    group["feature_contexts"][context["version"]] = context
                if start.isoformat() <= row["at_utc"] and previous is not None and not row["baseline"]:
                    event = _activity(previous, state, row)
                    if event:
                        group["activity"].append(event)
                previous = state
            state = previous
            group["limitations"].extend(state.get("limitations", []))
            group["trees"].append({
                "tree_id": tree["tree_id"], "path": tree["path"], "branch": state.get("branch"),
                "head": state.get("head"), "gone": state.get("gone", False),
                "last_seen": samples[-1]["at_utc"], "status": state.get("status"),
            })
            if state.get("files") and not state.get("gone"):
                group["ongoing"].append(dict(state, tree_id=tree["tree_id"],
                    evidence_id="ongoing:" + samples[-1]["sample_id"], at=samples[-1]["at_utc"],
                    sequence=samples[-1]["sample_order"],
                    context_version=state.get("feature_context", {}).get("version")))
            if samples[0]["at_utc"] >= start.isoformat():
                group["gaps"].append(f"{tree['tree_id']}: 首次观察只建立基线")
            commits = conn.execute("""
                SELECT c.payload_json,l.effective_at,l.first_seen FROM daily_commit_links l
                JOIN daily_commits c ON c.repo_id=l.repo_id AND c.sha=l.sha
                WHERE l.scope_key=? AND l.tree_id=? AND l.repo_id=? AND l.effective_at>=? AND l.effective_at<?
                ORDER BY l.effective_at,c.sha
            """, (key, tree["tree_id"], repo_id, start.isoformat(), end.isoformat()))
            for row in commits:
                facts = json.loads(row["payload_json"])
                context = facts.pop("feature_context", None)
                if context:
                    group["feature_contexts"][context["version"]] = context
                    facts["context_version"] = context["version"]
                prior = next((c for c in group["commits"] if c["sha"] == facts["sha"]), None)
                if prior is not None:
                    prior["first_seen"] = min(prior["first_seen"], row["first_seen"])
                    if tree["tree_id"] not in prior["tree_ids"]:
                        prior["tree_ids"].append(tree["tree_id"])
                    continue
                group["commits"].append(dict(facts, evidence_id=f"commit:{repo_id}:{facts['sha']}",
                                             effective_at=row["effective_at"], first_seen=row["first_seen"],
                                             tree_ids=[tree["tree_id"]]))
                group["limitations"].extend(facts.get("limitations", []))
        if not eligible_trees:
            group["gaps"].append(f"{project.project_id}: 没有可核对的窗口证据")
        seen = {t["tree_id"] for t in group["trees"]}
        for wt in project.worktrees:
            if wt.scan and wt.worktree_id not in seen and not excluded(wt.path):
                group["trees"].append({
                    "tree_id": wt.worktree_id, "path": str(Path(wt.path).resolve()),
                    "branch": None, "head": None, "gone": False,
                    "last_seen": None, "status": "unobserved",
                })
        _coverage(conn, key, start, end, now, group)
        for error in conn.execute("SELECT payload_json FROM events WHERE kind='daily_evidence_error' "
                                  "AND project_id=? AND at_utc>=? AND at_utc<?",
                                  (project.project_id, start.isoformat(), end.isoformat())):
            payload = json.loads(error[0])
            group["gaps"].append(f"{payload.get('worktree_id')}: 日报证据采集失败")
    for group in groups.values():
        group["commits"].sort(key=lambda c: (c["effective_at"], c["sha"]))
        group["activity"].sort(key=_event_order)
        group["evidence_ids"] = [x["evidence_id"] for x in group["commits"] + group["activity"] + group["ongoing"]]
        group["changed"] = bool(group["commits"] or group["activity"])
        group["gaps"] = list(dict.fromkeys(group["gaps"]))
        group["limitations"] = list(dict.fromkeys(group["limitations"]))
        used_contexts = {e.get("context_version") for e in group["commits"] + group["activity"] + group["ongoing"]}
        group["feature_contexts"] = {k: v for k, v in group["feature_contexts"].items() if k in used_contexts}
        # Link an observed edit to a commit only when the stored content hash agrees.
        for event in group["activity"]:
            event["covered_by_commits"] = sorted({
                commit["sha"] for commit in group["commits"]
                if ((event["kind"] in {"head_change", "resolved"} and event["head"] == commit["sha"])
                     or (event["at"] <= commit["first_seen"] and event["files"]
                         and all(any(cf["path"] == f["path"] and f.get("content_hash")
                            and cf.get("content_hash") == f["content_hash"] for cf in commit["files"])
                        for f in event["files"])))
            })
            event["followed_by_restore"] = next((
                later["evidence_id"] for later in group["activity"]
                if event["kind"] == "workspace" and later["kind"] == "restored"
                and later["tree_id"] == event["tree_id"] and _event_order(later) > _event_order(event)
                and later["head"] == event["head"] and set(event["paths"]) <= set(later["paths"])
            ), None)
            for file in event["files"]:
                file["covered_by_commits"] = sorted({c["sha"] for c in group["commits"]
                    if event["at"] <= c["first_seen"] and file.get("content_hash") and any(
                        f["path"] == file["path"] and f.get("content_hash") == file["content_hash"] for f in c["files"])})
                file["followed_by_restore"] = next((later["evidence_id"] for later in group["activity"]
                    if event["kind"] == "workspace" and not file.get("resolution_kind")
                    and later["tree_id"] == event["tree_id"] and _event_order(later) > _event_order(event)
                    and later["head"] == event["head"] and any(
                        f["path"] == file["path"] and (later["kind"] == "restored" or f.get("resolution_kind") == "restored")
                        for f in later["files"])), None)
    packet = {
        "schema": "xiaomao-daily-evidence-v2", "scope": scope_set(cfg),
        "window": {"date": date, "key": window_key(cfg, date), "timezone": cfg.timezone,
                   "start": start.isoformat(), "end": end.isoformat()},
        "generated_at": now.isoformat(), "preview": now < end,
        "projects": list(groups.values()),
        "candidates": getattr(cfg, "_inventory_candidates", []),
        "inventory_errors": getattr(cfg, "_inventory_errors", []),
    }
    packet["coverage"] = ("partial" if not groups or packet["inventory_errors"] or packet["candidates"]
                          or any(g["gaps"] or g["limitations"] for g in groups.values()) else "complete")
    # Presentation timestamps do not invalidate model reuse for unchanged evidence.
    fingerprint = {k: v for k, v in packet.items() if k not in {"generated_at", "preview"}}
    packet["evidence_hash"] = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    return packet


def project_lines(project) -> list[str]:
    lines = []
    for item in project["commits"]:
        kind = "合并提交" if item["kind"] == "merge" else "提交"
        lines.append(f"{kind}：{item['subject']}（{item['sha'][:12]}）")
    uncovered = [a for a in project["activity"] if not a["covered_by_commits"] and not a.get("followed_by_restore")]
    kinds = {"workspace": "工作区修改", "restored": "观察到修改后恢复至原提交",
             "resolved": "工作区差异消失，HEAD 已变化", "gone": "工作树已消失",
             "head_change": "分支或提交指针变化，具体切换或同步操作未知"}
    seen = set()
    for item in uncovered:
        identity = (item["kind"], item["tree_id"], tuple(item["paths"]))
        if identity in seen:
            continue
        seen.add(identity)
        detail = "、".join(item["paths"][:5]) or (item["head"] or "unborn")[:12]
        lines.append(f"{kinds[item['kind']]}：{item['tree_id']} · " + detail)
    return lines


def render_evidence(bundle: dict, notes: dict | None = None) -> str:
    notes = notes or {}
    window = bundle["window"]
    tz = ZoneInfo(window["timezone"])
    start = datetime.fromisoformat(window["start"]).astimezone(tz)
    end = datetime.fromisoformat(window["end"]).astimezone(tz)
    changed = sum(p["changed"] for p in bundle["projects"])
    lines = [f"小猫日报 {window['date']}", f"时区：{window['timezone']}",
             f"窗口：{start.isoformat()} → {end.isoformat()}（左闭右开）",
             f"生成时间（UTC）：{bundle['generated_at']}",
             f"覆盖：{bundle['coverage']}" + (" · 截止时间前预览" if bundle["preview"] else ""),
             "", "今日总览", "--------",
             f"{len(bundle['projects'])} 个项目，{changed} 个项目有新增观察。",
             "只报告有证据的个人项目变化。"]
    if not changed:
        lines.append("已覆盖部分未记录到新变化；遗留修改见下方，不据此推断没有工作。")
    lines.extend(["", "各项目变化", "--------"])
    for project in bundle["projects"]:
        lines.extend(["", f"项目：{project['display_name']}（{project['project_id']}）"])
        note = notes.get(project["repo_id"], {})
        bullets = note.get("bullets") or project_lines(project)
        for bullet in bullets[:5]:
            lines.append("- " + (bullet["text"] if isinstance(bullet, dict) else bullet))
            if isinstance(bullet, dict):
                lines.append("  解读来源：" + "、".join(bullet["evidence_ids"]))
        if not bullets:
            lines.append("- 未记录到新增变化。")
        if note.get("error"):
            lines.append(f"- 模型解读缺失：{note['error']}，以下保留规则证据。")
        lines.append("技术证据（已核实）")
        if len(project["trees"]) > 1:
            lines.append("其他工作树：")
        for tree in project["trees"]:
            branch = (tree["branch"] or "detached").removeprefix("refs/heads/")
            if tree["status"] == "unobserved":
                branch = "窗口内无可用状态"
            lines.append(f"  工作树：{tree['tree_id']} · {branch} · {(tree['head'] or 'unknown')[:12]}"
                         + (" · gone" if tree["gone"] else ""))
            lines.append(f"    目录：{tree['path']} · 最后核对：{tree['last_seen']}")
        for commit in project["commits"]:
            lines.append(f"  [{commit['evidence_id']}] {commit['sha']} · {commit['subject']} · "
                         f"提交时间 {commit['committed_at']} · 首次观察 {commit['first_seen']}")
            lines.append("    路径：" + "、".join(f["path"] for f in commit["files"]))
            lines.append("    来源工作树：" + "、".join(commit["tree_ids"]))
            if commit["kind"] == "merge":
                lines.append("    合并父提交：" + "、".join(commit["parents"]))
        for event in project["activity"]:
            lines.append(f"  [{event['evidence_id']}] {event['at']} · {event['kind']} · " + "、".join(event["paths"]))
            if event["covered_by_commits"]:
                lines.append("    已合并叙述至提交：" + "、".join(event["covered_by_commits"]))
            if event.get("followed_by_restore"):
                lines.append("    后续恢复证据：" + event["followed_by_restore"])
    lines.extend(["", "尚未提交的工作", "--------"])
    unfinished = False
    for project in bundle["projects"]:
        for state in project["ongoing"]:
            unfinished = True
            lines.append(f"- {project['display_name']} · {state['tree_id']}："
                         f"staged {state['staged']} / unstaged {state['unstaged']} / untracked {state['untracked']}")
            lines.append("  " + "、".join(f["path"] for f in state["files"]))
    if not unfinished:
        lines.append("- 已覆盖工作树的最后观察状态没有未提交修改；未覆盖部分无法判断。")
    lines.extend(["", "采集缺口（未核实）", "--------"])
    for project in bundle["projects"]:
        for gap in project["gaps"] + project["limitations"]:
            lines.append(f"- {project['display_name']}：{gap}")
    for row in bundle["candidates"]:
        lines.append(f"- 未纳入的候选仓库：{row['path']}")
    lines.extend("- " + e for e in bundle["inventory_errors"])
    lines.extend(["- 两次采集之间改动又撤销、未保存到磁盘的编辑不可见。",
                  "- 测试：unknown；部署：unknown。", "", "模型解读", "--------"])
    if not any(n.get("accepted") for n in notes.values()):
        lines.append("本报告由规则程序生成，未获得有效的模型解读。" if notes
                     else "本报告由规则程序生成，没有调用本地模型。")
    else:
        lines.append("各项目短句含本地模型解读，技术事实保持来源引用。")
    return "\n".join(lines) + "\n"


def render_bundle(bundle: dict, notes: dict | None = None) -> str:
    from xiaomao.feature_render import render_summary
    return render_summary(bundle, notes or {})
