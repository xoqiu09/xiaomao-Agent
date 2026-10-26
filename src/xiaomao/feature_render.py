"""One functional record set shared by the report, evidence view and menu."""
from __future__ import annotations

import json
from datetime import date as Date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from xiaomao.feature_daily import feature_records, project_units, record_sentence

DELIVERY = {"committed": "已提交；合并和正式启用未核实", "uncommitted": "尚未提交",
            "mixed": "部分已提交，仍有未提交工作", "observed": "已观察；交付状态待确认"}
UNKNOWN_NAMES = {"相关功能", "功能归属待确认", "工作状态"}


def ordered_records(project, note=None):
    return sorted(feature_records(project, note), key=lambda r: (
        r["interpretation"] != "model_static", -r["priority"], r["feature_name"] in UNKNOWN_NAMES, r["record_id"]))


def unknown_sentence(records):
    kinds = {r["progress"] for r in records}
    areas = []
    if kinds & {"implementation_changed", "continuing", "unclear"}:
        areas.append("实现逻辑")
    if "design_only" in kinds:
        areas.append("说明或设计")
    if "maintenance" in kinds:
        areas.append("内部维护")
    text = "记录到未解读的" + "、".join(areas or ["工作状态"]) + "调整，具体行为影响尚未确认；完整差异见「查看依据」。"
    if "restored" in kinds:
        text += "其中观察到修改后内容恢复，不计为新增能力。"
    states = sorted({r["after"] for r in records if r["feature_name"] == "工作状态"})
    if states:
        text += "观察状态：" + " ".join(states[:2])
    deliveries = {r["delivery"] for r in records}
    if "mixed" in deliveries or {"committed", "uncommitted"} <= deliveries:
        deliveries -= {"committed", "uncommitted"}
        deliveries.add("mixed")
    return text + "交付：" + "；".join(DELIVERY[d] for d in sorted(deliveries)) + "。"


def today_sentences(project, note=None, *, limit=5):
    rows = [r for r in ordered_records(project, note) if r["today"]]
    named = [r for r in rows if r["feature_name"] not in UNKNOWN_NAMES]
    unknown = [r for r in rows if r["feature_name"] in UNKNOWN_NAMES]
    sentences = [record_sentence(r) for r in named[:limit]]
    if unknown and len(sentences) < limit:
        sentences.append(unknown_sentence(unknown))
    return sentences


def author_free(value):
    """Hide attribution fields from personal-project report presentation."""
    if isinstance(value, dict):
        return {k: author_free(v) for k, v in value.items() if k not in {"author", "authors", "authored_at"}}
    if isinstance(value, list):
        return [author_free(v) for v in value]
    return value


def render_summary(bundle, notes):
    window = bundle["window"]
    tz = ZoneInfo(window["timezone"])
    start, end = [datetime.fromisoformat(window[k]).astimezone(tz).isoformat() for k in ("start", "end")]
    records = [(p, ordered_records(p, notes.get(p["repo_id"]))) for p in bundle["projects"]]
    changed = [(p, r) for p, rs in records for r in rs if r["today"]]
    changed.sort(key=lambda pair: (pair[1]["interpretation"] != "model_static", -pair[1]["priority"]))
    lines = [f"小猫日报 {window['date']}", f"时区：{window['timezone']}",
             f"窗口：{start} → {end}（左闭右开）", f"生成时间（UTC）：{bundle['generated_at']}",
             f"覆盖：{bundle['coverage']}" + (" · 截止时间前预览" if bundle["preview"] else ""),
             "", "今日总览", "--------",
             f"观察 {len(records)} 个项目，{sum(p['changed'] for p, _ in records)} 个项目记录到变化。"]
    overview = [(p, r) for p, r in changed if r["feature_name"] not in UNKNOWN_NAMES]
    for project, record in overview[:3]:
        lines.append(f"- {project['display_name']} · {record_sentence(record)}")
    if not changed:
        lines.append("- 已覆盖部分未记录到新变化；遗留工作列在下方。")
    elif not overview:
        lines.append("- 已记录项目变化，具体行为影响尚未确认；各项目待解读内容见下方。")
    lines.extend(["", "各项目功能变化", "--------"])
    for project, rows in records:
        lines.extend(["", f"项目：{project['display_name']}"])
        today = [r for r in rows if r["today"]]
        named = [r for r in today if r["feature_name"] not in UNKNOWN_NAMES]
        unknown = [r for r in today if r["feature_name"] in UNKNOWN_NAMES]
        for record in named[:5]:
            lines.append("- " + record["feature_name"])
            if record["before"]:
                lines.append("  之前：" + record["before"])
            lines.append("  变化：" + record["after"])
            if record["impact"]:
                lines.append("  使用影响：" + record["impact"])
            elif record["interpretation"] == "rule":
                lines.append("  使用影响：尚未获得有效解读，不能据此判断功能已完成。")
            lines.append(f"  交付：{DELIVERY[record['delivery']]}。")
            if record["variant"]:
                lines.append("  不同工作树保留了不同实现，分开呈现；对应来源见依据。")
            if record.get("partly_uninterpreted"):
                lines.append("  同功能还有其他调整待确认，完整条目保留在依据中。")
        if len(named) > 5:
            lines.append(f"- 另有 {len(named) - 5} 项已归类变化，完整条目及来源见「查看依据」。")
        if unknown:
            lines.append("- " + unknown_sentence(unknown))
        if not today:
            lines.append("- 本窗口未观察到新增推进。")
    lines.extend(["", "仍在推进的功能", "--------"])
    ongoing = False
    for project, rows in records:
        unknown = [r for r in rows if r["has_ongoing"] and r["feature_name"] in UNKNOWN_NAMES]
        if unknown:
            ongoing = True
            suffix = "其中有本窗口新观察，具体功能影响待确认。" if any(r["today"] for r in unknown) else "本窗口没有新的推进证据，不计为今日成果。"
            lines.append(f"- {project['display_name']}：保留未提交工作，功能归属待确认（{len(unknown)} 组依据，详见「查看依据」）。{suffix}")
        named = [r for r in rows if r["has_ongoing"] and r["feature_name"] not in UNKNOWN_NAMES]
        for record in named[:5]:
            if record["has_ongoing"]:
                ongoing = True
                suffix = "本窗口有新的观察。" if record["today"] else "本窗口没有新的推进证据，不计为今日成果。"
                lines.append(f"- {project['display_name']} · {record['feature_name']}：最后观察时有未提交工作。{suffix}")
        if len(named) > 5:
            lines.append(f"- {project['display_name']}：另有 {len(named) - 5} 项未提交功能工作，详见「查看依据」。")
    if not ongoing:
        lines.append("- 已覆盖工作树最后观察时没有未提交修改；未覆盖部分无法判断。")
    lines.extend(["", "待确认与采集缺口", "--------"])
    for project, rows in records:
        note = notes.get(project["repo_id"], {})
        if note.get("error"):
            lines.append(f"- {project['display_name']}：模型解读缺失或不完整：{note['error']}。")
        if any(r["interpretation"] == "rule" and r["progress"] in {"unclear", "implementation_changed"} for r in rows):
            lines.append(f"- {project['display_name']}：部分功能影响待确认，已记录相关修改，可查看依据。")
        if not project.get("feature_contexts") and project["changed"]:
            lines.append(f"- {project['display_name']}：缺少当时的项目背景，未用当前代码补写历史。")
        gaps, limitations = project["gaps"], project["limitations"]
        if "该窗口没有扫描覆盖证据" in gaps:
            lines.append(f"- {project['display_name']}：该窗口没有扫描覆盖证据。")
        elif gaps:
            lines.append(f"- {project['display_name']}：采集存在缺口或失败，涉及 {len(gaps)} 项记录，具体时间及工作树见依据。")
        if limitations:
            lines.append(f"- {project['display_name']}：部分内容因读取限制、脱敏或预算未纳入解读。")
    if bundle["candidates"]:
        lines.append(f"- {len(bundle['candidates'])} 个仓库尚未确认归属，未读取其代码。")
    if bundle["inventory_errors"]:
        lines.append(f"- 仓库清单有 {len(bundle['inventory_errors'])} 项检查失败，详见依据。")
    lines += ["- 已核实部分来自保存的提交与观察；功能解释属于静态解读。",
              "- 测试：unknown；部署：unknown（未核实）。",
              "- 两次采集之间改动又撤销、未保存到磁盘的编辑不可见。", ""]
    if any(n.get("accepted") for n in notes.values()):
        lines.append("本报告含本地模型功能解读；每条解读附有原文支持，运行效果仍需验证。")
    else:
        lines.append("本报告由规则程序生成，未获得有效的模型解读。" if notes
                     else "本报告由规则程序生成，没有调用本地模型。")
    lines += ["", f"查看依据：../daily-evidence/{window['date']}.txt（完整功能条目、提交、工作树及差异）",
              "也可从小猫菜单打开「查看日报依据」。"]
    return "\n".join(lines) + "\n"


def render_details(bundle, notes):
    from xiaomao.daily import render_evidence
    # The plain evening report is the user-facing body; the former full
    # functional summary stays here so the evidence is not reduced.
    lines = [render_evidence(bundle), "功能日报（完整版）", "====================",
             render_summary(bundle, notes), "完整功能条目及原文支持", "===================="]
    for project in bundle["projects"]:
        lines.append("项目：" + project["display_name"])
        lines.append(json.dumps(author_free({"records": feature_records(project, notes.get(project["repo_id"])),
                                 "contexts": project.get("feature_contexts", {}),
                                 "units": project_units(project),
                                 "source_diffs": [{"evidence_id": e.get("evidence_id"),
                                     "files": [{k: f.get(k) for k in ("path", "patch", "availability")}
                                               for f in e.get("files", [])]}
                                      for e in project["commits"] + project["activity"] + project["ongoing"]]}),
                                ensure_ascii=False, indent=2))
    text = "\n".join(lines) + "\n"
    # Match the bounded reader. The complete packet remains in daily_windows;
    # do not silently publish a report that cannot be read through scope checks.
    encoded = text.encode()
    if len(encoded) > 1800 * 1024:
        text = encoded[:1800 * 1024].decode("utf-8", errors="ignore") + "\n[依据展示达到上限；原观察仍保留]\n"
    return text


def details_path(home: Path, date: str):
    return home / "reports" / "daily-evidence" / f"{date}.txt"


def write_details(cfg, bundle, notes):
    from xiaomao.daily_jobs import preserve_report
    from xiaomao.menu_briefing import _atomic_write
    from xiaomao.report_access import bind_report
    dest = details_path(Path(cfg.home), bundle["window"]["date"])
    if dest.parent.resolve() != Path(cfg.home).resolve() / "reports/daily-evidence":
        raise ValueError("依据目录越出数据 home")
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = render_details(bundle, notes)
    preserve_report(dest, text)
    _atomic_write(dest, text)
    bind_report(dest, text, cfg, "daily-evidence", daily_bundle=bundle)
    return dest


# ---- Plain evening report (core plan §3) ----------------------------------

WEEKDAYS = "一二三四五六日"
PLAIN_MAX_PROJECTS = 3
PLAIN_MAX_ITEMS = 3
PLAIN_MAX_LINES = 40
# Core plan §3 rule 7: technical wording that stays in the evidence file.
BANNED_TERMS = ("未核实", "范围哈希", "观察 ID", "观察ID", "覆盖 partial", "功能归属待确认", "交付状态待确认")
UNCLEAR = "这条看不出具体做了什么"
PROGRESS_PLAIN = {"design_only": "更新了说明或设计文档", "maintenance": "做了内部维护",
                  "restored": "改过又改回去了，不算新功能"}


def _state_label(state, has_remote):
    from xiaomao.delivery_state import LABELS
    if state is None or (has_remote is False and state not in {"uncommitted", "local"}):
        return ""
    return LABELS[state]


def _plain_text(record):
    """Validated model wording when present; otherwise rule wording a reader can use."""
    named = record["feature_name"] not in UNKNOWN_NAMES
    if record["interpretation"] == "model_static":
        text = record["after"].rstrip("。")
        if record["impact"]:
            text += "，" + record["impact"].rstrip("。")
    elif record["progress"] in PROGRESS_PLAIN:
        text = PROGRESS_PLAIN[record["progress"]]
        text = f"{record['feature_name']}：{text}" if named else text
    else:
        text = f"{record['feature_name']}有改动，{UNCLEAR}" if named else UNCLEAR
    if any(term in text for term in BANNED_TERMS):
        text = UNCLEAR
    return text


def _commit_text(commit):
    """Rule wording for a commit: the author's own subject line, already redacted."""
    subject = (commit.get("subject") or "").strip()
    if not subject or any(term in subject for term in BANNED_TERMS):
        return UNCLEAR
    return subject if len(subject) <= 60 else subject[:59] + "…"


def _plain_items(project, notes):
    """Validated model records first; every other change gets a rule line.

    Rule lines are one per commit (its own state) plus one for uncommitted
    work, so a label never mixes commits that sit in different places.
    """
    from xiaomao.delivery_state import record_state
    units = {u["unit_id"]: u for u in project_units(project)}
    info = project.get("delivery_state") or {}
    has_remote = info.get("has_remote")
    items, covered, pending_covered = [], set(), False
    note = notes.get(project["repo_id"])
    if note and note.get("records") is not None:
        for record in ordered_records(project, note):
            if not record["today"] or record["interpretation"] != "model_static":
                continue
            own = [units[u] for u in record.get("unit_ids", []) if u in units]
            covered.update(u["sha"] for u in own if u["origin"] == "commit")
            pending_covered |= any(u["origin"] != "commit" for u in own)
            state = record_state(record, units, project)
            items.append({"text": _plain_text(record), "state": state, "feature": record["feature_name"],
                          "label": _state_label(state, has_remote), "model": True})
    for commit in reversed(project["commits"]):
        if commit["sha"] in covered:
            continue
        state = info.get("commits", {}).get(commit["sha"])
        items.append({"text": _commit_text(commit), "state": state,
                      "label": _state_label(state, has_remote), "model": False})
    dirty = 0 if pending_covered else info.get("dirty_files", 0)
    if dirty and not items:
        items.append({"text": f"{dirty} 处改动还没提交", "state": "uncommitted",
                      "label": _state_label("uncommitted", has_remote), "model": False})
        dirty = 0
    elif not items and project["activity"]:
        items.append({"text": "有过改动，但没有留下新的提交或未提交内容", "state": None,
                      "label": "", "model": False})
    return items, dirty


def _project_title(project, items=()):
    suffix = "（无远端）" if (project.get("delivery_state") or {}).get("has_remote") is False else ""
    features = {i["feature"] for i in items if i.get("feature")} - UNKNOWN_NAMES
    feature = f" · {features.pop()}" if len(features) == 1 else ""
    return project["display_name"] + suffix + feature


def _item_line(prefix, item):
    return f"{prefix}{item['text']}" + (f"  {item['label']}" if item["label"] else "")


def plain_next_steps(bundle):
    """Rule-generated next steps, most actionable first; the model never picks these."""
    steps = []
    for project in bundle["projects"]:
        info = project.get("delivery_state") or {}
        name = project["display_name"]
        if info.get("unpushed") and info.get("has_remote"):
            steps.append((0, f"{name}：推送本地的 {len(info['unpushed'])} 个提交", len(info["unpushed"])))
        if info.get("dirty_files"):
            advice = "，建议分批提交" if info["dirty_files"] >= 20 else "，记得提交"
            steps.append((1, f"{name}：{info['dirty_files']} 处未提交改动{advice}", info["dirty_files"]))
        pushed = sum(s == "pushed" for s in info.get("commits", {}).values())
        if info.get("default_ref") and pushed:
            branch = info["default_ref"].rsplit("/", 1)[-1]
            steps.append((2, f"{name}：已推送的分支还没合进 {branch}", pushed))
    # Within a kind, the larger backlog first (72 dirty files before 1).
    steps.sort(key=lambda s: (s[0], -s[2]))
    # One step of each kind first (push, commit, merge), then the rest.
    firsts = [next(s for s in steps if s[0] == kind) for kind in (0, 1, 2) if any(s[0] == kind for s in steps)]
    return [s[1] for s in firsts + [s for s in steps if s not in firsts]]


def _issue_count(bundle, notes):
    count = len(bundle["inventory_errors"]) + len(bundle["candidates"])
    for project in bundle["projects"]:
        count += len(project["gaps"]) + len(project["limitations"])
        count += len((project.get("delivery_state") or {}).get("errors", []))
        count += bool(notes.get(project["repo_id"], {}).get("error"))
    return count


def plain_summary_line(bundle):
    """First line of the plain report; also the notification headline."""
    active = sum(p["changed"] for p in bundle["projects"])
    unpushed = [(p["display_name"], len((p.get("delivery_state") or {}).get("unpushed", [])))
                for p in bundle["projects"]]
    unpushed = [(name, n) for name, n in unpushed if n]
    line = f"今天 {active} 个项目有进展" if active else "今天没有新的进展"
    if len(unpushed) == 1:
        line += f"，{unpushed[0][0]} 有 {unpushed[0][1]} 个提交还没推送"
    elif unpushed:
        line += f"，{len(unpushed)} 个项目共 {sum(n for _, n in unpushed)} 个提交还没推送"
    return line + "。"


def render_plain_summary(bundle, notes, *, supplemented=False):
    """Plain evening report: ≤40 lines, ≤3 projects × ≤3 items, each with a state label."""
    notes = notes or {}
    day = Date.fromisoformat(bundle["window"]["date"])
    title = f"小猫晚报 · {day.month}月{day.day}日（周{WEEKDAYS[day.weekday()]}）"
    title += " · 截止前预览" if bundle.get("preview") else ""
    title += " · 已补充解读" if supplemented else ""
    lines = [title, plain_summary_line(bundle), ""]
    active = [(p, *_plain_items(p, notes)) for p in bundle["projects"] if p["changed"]]
    active = [row for row in active if row[1]]
    # Projects with readable, interpreted progress first; then the busiest.
    active.sort(key=lambda row: (-sum(i["model"] for i in row[1]),
                                 -sum(UNCLEAR not in i["text"] for i in row[1]), -len(row[1])))
    for project, items, dirty in active[:PLAIN_MAX_PROJECTS]:
        lines.append(f"★ {_project_title(project, items)}")
        lines.extend(_item_line("  · ", item) for item in items[:PLAIN_MAX_ITEMS])
        if len(items) > PLAIN_MAX_ITEMS:
            lines.append(f"  （另有 {len(items) - PLAIN_MAX_ITEMS} 项变化，见依据）")
        if dirty:
            lines.append(f"  （另有 {dirty} 处改动还没提交）")
        lines.append("")
    for project, items, _dirty in active[PLAIN_MAX_PROJECTS:]:
        lines.append(_item_line(f"· {_project_title(project)}：", items[0]))
    if active[PLAIN_MAX_PROJECTS:]:
        lines.append("")
    steps = plain_next_steps(bundle)
    if steps:
        lines.append("明天从这里继续")
        lines.extend(f"  {n}. {text}" for n, text in enumerate(steps[:3], 1))
        lines.append("")
    quiet = [p["display_name"] for p in bundle["projects"] if not p["changed"]]
    if quiet:
        lines.append("今天没动：" + "、".join(quiet))
    if issues := _issue_count(bundle, notes):
        lines.append(f"{issues} 处采集问题，详见依据 → 菜单「查看依据」")
    lines.append("MacBook 上还没推送的工作，小猫看不到。")
    if len(lines) > PLAIN_MAX_LINES:
        lines = lines[:PLAIN_MAX_LINES - 1] + ["（内容较多，其余见依据）"]
    return "\n".join(lines) + "\n"
