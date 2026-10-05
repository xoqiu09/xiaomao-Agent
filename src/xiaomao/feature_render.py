"""One functional record set shared by the report, evidence view and menu."""
from __future__ import annotations

import json
from datetime import datetime
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
    lines = [render_evidence(bundle), "完整功能条目及原文支持", "===================="]
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
