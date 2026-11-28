"""Run the fixed eval set against a local model, or dry-run the validators."""

from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any

from xiaomao.config import AppConfig
from xiaomao.eval_samples import Sample, samples
from xiaomao.summarize import _NEGATION_MARKERS, summarize_or_degrade, validate_model_json


def host_pressure() -> dict[str, Any]:
    """Memory / swap snapshot. Best-effort; unknown fields stay null."""
    out: dict[str, Any] = {
        "memory_bytes": None,
        "swap_used": None,
        "swap_total": None,
        "pages_free": None,
        "pages_speculative": None,
        "pages_compressor": None,
    }
    try:
        proc = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip().isdigit():
            out["memory_bytes"] = int(proc.stdout.strip())
    except OSError:
        pass
    try:
        proc = subprocess.run(
            ["sysctl", "-n", "vm.swapusage"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            out["swap_raw"] = proc.stdout.strip()
            text = proc.stdout
            # "total = 2048.00M  used = 412.50M  free = 1635.50M"
            for key, dest in (("total =", "swap_total"), ("used =", "swap_used")):
                if key in text:
                    token = text.split(key, 1)[1].split()[0]
                    out[dest] = token
    except OSError:
        pass
    try:
        proc = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5, check=False)
        if proc.returncode == 0:
            mapping = {
                "Pages free": "pages_free",
                "Pages speculative": "pages_speculative",
                "Pages stored in compressor": "pages_compressor",
            }
            for line in proc.stdout.splitlines():
                for label, dest in mapping.items():
                    if line.startswith(label):
                        digits = "".join(ch for ch in line.split(":", 1)[-1] if ch.isdigit())
                        if digits:
                            out[dest] = int(digits)
    except OSError:
        pass
    return out


_CLAIM_NEGATION_PREFIX = re.compile(
    "(?:" + "|".join(re.escape(marker) for marker in _NEGATION_MARKERS if marker not in {"未知", "unknown"})
    + r"|尚无|暂无|缺乏|无证据|不足以)"
    r"(?:\s*(?:证据|足够|表明|显示|证明|说明|确认|断定|判断|推断|能|说))*"
    r'\s*[:：]?\s*(?:\\?["“‘「『]\s*)?$',
    re.IGNORECASE,
)
_CLAIM_NEGATION_SUFFIX = re.compile(
    r'\s*(?:\\?["”’」』]\s*)?(?:(?:这一|的)?(?:说法|结论|断言))'
    r"\s*(?:没有|缺乏|仍无|尚无|无)(?:足够)?证据"
)


def _is_asserted(statement: str, needle: str) -> bool:
    for match in re.finditer(re.escape(needle), statement):
        if _CLAIM_NEGATION_PREFIX.search(statement[:match.start()]):
            continue
        if _CLAIM_NEGATION_SUFFIX.match(statement[match.end():]):
            continue
        return True
    return False


def _assertive_hits(text: str, needles: tuple[str, ...]) -> list[str]:
    if not needles or not text:
        return []
    statements = re.split(r"[。！？；!?;\n]+", text)
    return [
        needle for needle in needles
        if needle and any(_is_asserted(statement, needle) for statement in statements)
    ]


def score_sample(sample: Sample, result: dict[str, Any]) -> dict[str, Any]:
    blob = json.dumps(result.get("payload") or result.get("raw") or {}, ensure_ascii=False, indent=2)
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
    usable = 0
    started = time.monotonic()
    pressure_before = host_pressure()
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
        if scored["ok"] and not scored["serious_factual_error"] and any(
            item["text"].strip()
            for section in ("interpretations", "suggestions", "unknowns")
            for item in result["payload"][section]
        ):
            usable += 1
        scored["latency_s"] = round(time.monotonic() - t0, 3)
        scored["model_note_head"] = (result.get("model_note") or "")[:240]
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        scored["parse_source"] = raw.get("parse_source")
        scored["eval_count"] = raw.get("eval_count")
        rows.append(scored)
    elapsed = time.monotonic() - started
    serious = [r for r in rows if r["serious_factual_error"]]
    pressure_after = host_pressure()
    return {
        "model": model,
        "n": len(rows),
        "ok": len([r for r in rows if r["ok"]]),
        "degraded": len([r for r in rows if r["degraded"]]),
        "usable": usable,
        "serious_factual_errors": len(serious),
        "elapsed_s": round(elapsed, 3),
        "host_pressure_before": pressure_before,
        "host_pressure_after": pressure_after,
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
