"""Answer a question with a local model, inside fixed boundaries.

Readable material (all read-only): saved daily windows, the existing project
briefing digests (purpose line only), and the latest handoffs in the
registered document library. No scan, no source code, no raw documents.

Three kinds of answer: a project answer must name its sources; a general
answer is labelled as the model's own, without sources; when the model is not
sure it does not answer and hands a ready-to-paste question to Codex/Claude.
Nothing is written to the database and no notification is sent.
"""
from __future__ import annotations

import json
import sys
from datetime import date as Date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ASK_DEFAULT_DAYS = 7
ASK_MAX_DAYS = 14
PACKET_CHAR_BUDGET = 16_000
MAX_COMMITS_PER_PROJECT = 12
MAX_BRIEF_CHARS = 400
MAX_HANDOFFS = 5
MAX_HANDOFF_CHARS = 1500
KINDS = ("project", "general", "defer")

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "answer": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "not_in_records": {"type": "boolean"},
        "handoff_question": {"type": "string"},
    },
    "required": ["kind", "answer", "sources", "not_in_records", "handoff_question"],
}

SYSTEM = (
    "你是小猫，用户的个人项目进度秘书。用简体中文白话回答，不超过 8 句。只输出 JSON。"
    "资料只有用户消息里的三部分：records 是每天的晚报记录，project_briefs 是各项目是做什么的，"
    "handoffs 是用户保存的交接。每条资料都有 source 编号（记录用日期 YYYY-MM-DD）。"
    "问题分三种，填在 kind："
    "project：关于用户项目的问题，只根据资料回答，不要猜测；说出具体做了哪些事，不要只说「有更新」；"
    "提到代码时说明它在哪：未提交、仅本机、已推送或已合并。sources 填用到的 source 编号。"
    "资料里没有答案时，not_in_records 填 true，answer 简短说明没有记录。"
    "general：与项目无关、而且你很有把握的常识性问题，直接回答，sources 留空。"
    "defer：需要查资料、看代码、判断对错、做决定，或者你没有把握的问题，不要回答；"
    "answer 用一句话说明为什么没把握，handoff_question 写一句用户可以直接发给 Codex 或 Claude 的问题。"
    "records 每天的 status 列出所有项目当天结束时的状态，包括当天没有变化的项目："
    "unpushed_commits 是只在本机、还没推送的提交数；uncommitted_files 是还没提交的文件数；"
    "pushed_not_merged 是已经推送、还没合进主分支的提交数，它们不算「没推送」。"
    "这些数量以最新一天的记录为准。"
)


def recent_bundles(conn, *, days: int, until: str) -> list[dict]:
    """The latest stored bundle of each window date in [until - days + 1, until]."""
    start = (Date.fromisoformat(until) - timedelta(days=days - 1)).isoformat()
    latest = {}
    for day, raw in conn.execute(
            "SELECT date, bundle_json FROM daily_windows WHERE date BETWEEN ? AND ? ORDER BY completed_at",
            (start, until)):
        latest[day] = raw
    return [json.loads(latest[day]) for day in sorted(latest)]


def _matches(project: dict, wanted: str | None) -> bool:
    if not wanted:
        return True
    wanted = wanted.casefold()
    return wanted in {str(project.get("project_id", "")).casefold(), str(project.get("display_name", "")).casefold()}


def project_facts(project: dict) -> dict:
    from xiaomao.feature_render import _commit_text, _state_label
    info = project.get("delivery_state") or {}
    states = info.get("commits", {})
    commits = [{"title": _commit_text(c), "where": _state_label(states.get(c["sha"]), info.get("has_remote"))}
               for c in reversed(project.get("commits", []))]
    facts = {"project": project["display_name"], "commits": commits[:MAX_COMMITS_PER_PROJECT]}
    if len(commits) > MAX_COMMITS_PER_PROJECT:
        facts["more_commits"] = len(commits) - MAX_COMMITS_PER_PROJECT
    return facts


def project_status(project: dict) -> dict | None:
    """End-of-day counts for any project, changed that day or not; None when all zero."""
    info = project.get("delivery_state") or {}
    status = {"uncommitted_files": info.get("dirty_files", 0), "unpushed_commits": len(info.get("unpushed", [])),
              "pushed_not_merged": sum(s == "pushed" for s in info.get("commits", {}).values())}
    if not any(status.values()):
        return None
    if info.get("has_remote") is False:
        status["no_remote"] = True
    return {"project": project["display_name"], **status}


def build_packet(bundles: list[dict], project: str | None = None) -> list[dict]:
    """Per-day facts the model may use; oldest days drop first to fit the budget."""
    from xiaomao.feature_render import plain_next_steps, plain_summary_line
    days = []
    for bundle in bundles:
        chosen = [p for p in bundle["projects"] if _matches(p, project)]
        if project and not chosen:
            continue
        days.append({"date": bundle["window"]["date"], "summary": plain_summary_line(bundle),
                     "projects": [project_facts(p) for p in chosen if p["changed"]],
                     "no_change": [p["display_name"] for p in chosen if not p["changed"]],
                     "status": [s for s in map(project_status, chosen) if s],
                     "next_steps": [s for s in plain_next_steps(bundle)
                                    if not project or any(s.startswith(p["display_name"] + "：") for p in chosen)][:3]})
    while len(days) > 1 and len(json.dumps(days, ensure_ascii=False)) > PACKET_CHAR_BUDGET:
        days.pop(0)
    return days


def project_briefs(cfg, project: str | None = None) -> list[dict]:
    """The purpose line of each existing briefing digest; nothing else of it is passed on."""
    from xiaomao.briefing import cache_paths
    from xiaomao.menu_briefing import contains_file_path
    from xiaomao.scope import project_exclusion_reason
    briefs = []
    for spec in cfg.projects:
        if project_exclusion_reason(spec) or not _matches(
                {"project_id": spec.project_id, "display_name": spec.display_name}, project):
            continue
        path = cache_paths(cfg, spec.project_id)[0]
        try:
            purpose = json.loads(path.read_text(encoding="utf-8"))["understanding"]["purpose"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(purpose, str) and purpose.strip() and not contains_file_path(purpose):
            briefs.append({"source": f"brief:{spec.project_id}", "project": spec.display_name or spec.project_id,
                           "purpose": purpose.strip()[:MAX_BRIEF_CHARS]})
    return briefs


def recent_handoffs(cfg) -> list[dict]:
    """Latest revisions in the registered document library, bounded; empty when none is registered."""
    if not cfg.docs_home:
        return []
    from xiaomao.documents import DocumentStore
    try:
        store = DocumentStore(Path(cfg.docs_home))
        handoffs = []
        for doc in store.list(limit=MAX_HANDOFFS)["documents"]:
            body = store.read(doc["document_id"], max_chars=MAX_HANDOFF_CHARS)
            handoffs.append({"source": f"doc:{doc['document_id']}", "title": body["title"],
                             "project": body["project_id"] or "个人资料", "updated_at": doc.get("updated_at"),
                             "content": body["content"]})
    except (OSError, ValueError, KeyError):
        return []
    return handoffs


def material(cfg, days: list[dict], project: str | None) -> tuple[dict, dict]:
    """The model's input and a label for every source id it may cite."""
    packet = {"records": days, "project_briefs": project_briefs(cfg, project),
              "handoffs": [] if project else recent_handoffs(cfg)}
    while packet["handoffs"] and len(json.dumps(packet, ensure_ascii=False)) > PACKET_CHAR_BUDGET:
        packet["handoffs"].pop()
    labels = {d["date"]: f"{int(d['date'][5:7])}月{int(d['date'][8:10])}日的晚报" for d in days}
    labels.update({b["source"]: f"{b['project']} 的项目说明" for b in packet["project_briefs"]})
    labels.update({h["source"]: f"交接《{h['title']}》" for h in packet["handoffs"]})
    return packet, labels


def validated(result: dict | None, sources: set[str]) -> dict | None:
    data = (result or {}).get("json")
    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
        return None
    kind = data.get("kind") if data.get("kind") in KINDS else "project"
    answer = data["answer"].strip()
    if kind == "defer":
        question = data.get("handoff_question")
        if not isinstance(question, str) or not question.strip():
            return None
        return {"kind": kind, "answer": answer[:600], "handoff_question": question.strip()[:300], "sources": []}
    if not answer:
        return None
    if kind == "general":
        return {"kind": kind, "answer": answer[:2000], "sources": []}
    cited = sorted({s for s in data.get("sources") or [] if isinstance(s, str) and s in sources})
    not_found = data.get("not_in_records") is True
    if not cited and not not_found:
        return None
    return {"kind": kind, "answer": answer[:2000], "sources": cited, "not_in_records": not_found}


def answer_question(cfg, question: str, packet: dict, labels: dict, client, model: str) -> dict:
    user = json.dumps({"question": question, **packet}, ensure_ascii=False)
    try:
        result = client.generate_json(model=model, system=SYSTEM, user=user, schema=SCHEMA)
    except Exception as exc:  # model failure never turns into an answer
        return {"ok": False, "error": f"本地模型调用失败（{type(exc).__name__}）"}
    checked = validated(result, set(labels))
    if checked is None:
        return {"ok": False, "error": "模型的回答没有注明依据，没有采用"}
    return {"ok": True, "model": model, "labels": labels, **checked}


def format_answer(result: dict) -> str:
    if result["kind"] == "defer":
        reason = result["answer"] or "这个问题我没有把握。"
        return f"{reason}\n\n建议去问 Codex 或 Claude，可以直接粘贴：\n{result['handoff_question']}\n"
    if result["kind"] == "general":
        return (f"{result['answer']}\n\n（这是模型自己的回答，没有依据小猫的记录；重要的事请再问 Codex 或 Claude。"
                f"模型 {result['model']}）\n")
    if result["not_in_records"] and not result["sources"]:
        return result["answer"] + "\n"
    cited = "、".join(result["labels"][s] for s in result["sources"])
    return f"{result['answer']}\n\n依据：{cited} · 模型 {result['model']} · 只用小猫已存的记录\n"


def ask_command(ns, home: Path, *, client_factory=None, today=None, out=sys.stdout, err=sys.stderr) -> int:
    from datetime import datetime, timezone
    from xiaomao.config import load_config
    from xiaomao.paths import layout
    from xiaomao.store import infer_paused, open_db_readonly
    cfg = load_config(home)
    cfg.home = str(home)
    allowed = list(dict.fromkeys([cfg.ask_model, *cfg.depth_candidates, cfg.lite_model]))
    model = ns.model or cfg.ask_model
    if model not in allowed:
        err.write(f"只能使用配置里登记的本地模型：{'、'.join(allowed)}\n")
        return 2
    days = max(1, min(ns.days or ASK_DEFAULT_DAYS, ASK_MAX_DAYS))
    today = today or datetime.now(timezone.utc).astimezone(ZoneInfo(cfg.timezone)).date().isoformat()
    db = layout(home)["db"]
    if not db.exists():
        err.write("还没有小猫的记录。\n")
        return 2
    with open_db_readonly(db) as conn:
        if infer_paused(conn):
            err.write("推理已暂停，没有调用模型。可用 xiaomao resume infer 恢复。\n")
            return 3
        records = build_packet(recent_bundles(conn, days=days, until=today), ns.project)
    if ns.project and not records:
        err.write(f"最近 {days} 天还没有这个项目的晚报记录。\n")
        return 2
    packet, labels = material(cfg, records, ns.project)
    started = None
    if client_factory is None:
        client, started = _local_client(cfg)
        if client is None:
            err.write("本地模型没有运行，也没能从外盘启动。可以先运行 scripts/user_serve_ollama.sh。\n")
            return 3
    else:
        client = client_factory(cfg)
    try:
        result = answer_question(cfg, ns.question, packet, labels, client, model)
    finally:
        if started is not None:
            started.terminate()
    if not result["ok"]:
        err.write(result["error"] + "。可以看晚报：xiaomao latest daily --print\n")
        return 3
    out.write(format_answer(result))
    return 0


def _local_client(cfg):
    """A client for the running Ollama, or one started just for this question."""
    from xiaomao.ollama_runtime import OllamaClient, inspect_running, models_dir_allowed, start_serve
    try:
        models_dir_allowed(cfg)
        if inspect_running(cfg)["reachable"]:
            return OllamaClient(cfg), None
        started = start_serve(cfg)
    except Exception:
        return None, None
    return (OllamaClient(cfg), started) if started is not None else (None, None)
