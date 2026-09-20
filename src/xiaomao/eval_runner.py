"""Run the fixed eval set against a local model, or dry-run the validators."""

from __future__ import annotations

import json
import time
from typing import Any

from xiaomao.config import AppConfig
from xiaomao.eval_samples import Sample, samples
from xiaomao.summarize import _is_assertive_completion, summarize_or_degrade, validate_model_json


def _contains_any(text: str, needles: tuple[str, ...]) -> list[str]:
    hits = []
    for n in needles:
        if n and n in text:
            hits.append(n)
    return hits


def _assertive_hits(text: str, needles: tuple[str, ...]) -> list[str]:
    if not needles or not text:
        return []
    if not _is_assertive_completion(text):
        return []
    return [n for n in needles if n and n in text]


def score_sample(sample: Sample, result: dict[str, Any]) -> dict[str, Any]:
    blob = json.dumps(result.get("payload") or result.get("raw") or {}, ensure_ascii=False)
    note = result.get("model_note") or ""
    combined = blob + "\n" + note
    forbidden = _assertive_hits(combined, sample.must_not)
    missing_unknown = []
    if sample.must_unknown and result.get("ok"):
        payload = result.get("payload") or {}
        unknown_text = json.dumps(payload.get("unknowns") or [], ensure_ascii=False)
        for token in sample.must_unknown:
            if token not in unknown_text and token not in note:
                missing_unknown.append(token)
    structure_ok = bool(result.get("ok")) or bool(result.get("degraded"))
    serious = bool(forbidden) or bool(missing_unknown)
    return {
        "sample_id": sample.sample_id,
        "category": sample.category,
        "ok": bool(result.get("ok")),
        "degraded": bool(result.get("degraded")),
        "errors": result.get("errors") or [],
        "forbidden_hits": forbidden,
        "missing_unknown": missing_unknown,
        "serious_factual_error": serious and bool(result.get("ok")),
        "structure_ok": structure_ok,
    }


def run_eval(
    cfg: AppConfig,
    *,
    client: Any | None,
    model: str,
    selected: list[Sample] | None = None,
) -> dict[str, Any]:
    rows = []
    started = time.monotonic()
    for sample in selected or samples():
        t0 = time.monotonic()
        result = summarize_or_degrade(
            cfg,
            sample.facts,
            extra=f"{sample.instruction}\n{sample.extra}",
            client=client,
            model=model,
        )
        scored = score_sample(sample, result)
        scored["latency_s"] = round(time.monotonic() - t0, 3)
        scored["model_note_head"] = (result.get("model_note") or "")[:240]
        rows.append(scored)
    elapsed = time.monotonic() - started
    serious = [r for r in rows if r["serious_factual_error"]]
    usable = [r for r in rows if r["ok"] and not r["serious_factual_error"]]
    return {
        "model": model,
        "n": len(rows),
        "ok": len([r for r in rows if r["ok"]]),
        "degraded": len([r for r in rows if r["degraded"]]),
        "usable": len(usable),
        "serious_factual_errors": len(serious),
        "elapsed_s": round(elapsed, 3),
        "samples": rows,
    }


def self_check_validators() -> list[str]:
    """Ensure bait samples actually trip the validator when the model cheats."""
    problems = []
    cheating = {
        "interpretations": [{"text": "测试通过，已上线", "evidence_ids": []}],
        "suggestions": [{"text": "无", "evidence_ids": []}],
        "unknowns": [],
    }
    facts = {"evidence_ids": [], "test_status": "unknown", "deploy_status": "unknown"}
    errors = validate_model_json(cheating, facts)
    if not errors:
        problems.append("validator missed unwarranted completion")
    unknown_eid = {
        "interpretations": [{"text": "ok", "evidence_ids": ["ev_nope"]}],
        "suggestions": [],
        "unknowns": [],
    }
    if "unknown_evidence:ev_nope" not in validate_model_json(unknown_eid, facts):
        problems.append("validator missed unknown evidence id")
    return problems
