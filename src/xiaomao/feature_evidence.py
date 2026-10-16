"""Small, sanitized before/after units captured at source observation time.

These are static evidence, never a program execution or a proof of correctness.
"""
from __future__ import annotations

import ast
import difflib
import json
import re
from pathlib import Path

from xiaomao.change_evidence import safe_text

MAX_SNAPSHOT = 4096
MAX_UNITS = 16
_DECL = re.compile(r"(?:async\s+)?(?:def|class|func|function|fn|interface|struct)\s+([\w.]+)|"
                   r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=.*(?:=>|function)")


def _symbol(lines, index):
    for line in reversed(lines[:index + 1]):
        match = _DECL.search(line)
        if match:
            return next(s for s in match.groups() if s)
    return ""


def _context(lines, index):
    signature = next((line.strip() for line in reversed(lines[:index + 1]) if _DECL.search(line)), "")
    return safe_text("\n".join([signature, *lines[max(0, index - 2):index + 3]]), 700)


def _kind(before, after, path):
    if Path(path).suffix.lower() in {".md", ".rst", ".txt"}:
        return "documentation"
    if path.endswith(".py"):
        try:
            if ast.dump(ast.parse(before)) == ast.dump(ast.parse(after)):
                return "maintenance"
        except (SyntaxError, ValueError, RecursionError):
            pass
    if [s.rstrip() for s in before.splitlines()] == [s.rstrip() for s in after.splitlines()]:
        return "maintenance"
    return "code"


def capture(before: str, after: str, path: str, *, layer="working") -> dict:
    # Redact whole sources before extracting snippets, including multiline keys.
    before, after = safe_text(before, 512 * 1024), safe_text(after, 512 * 1024)
    left, right = before.splitlines(), after.splitlines()
    kind, units = _kind(before, after, path), []
    truncated = False
    for ops in difflib.SequenceMatcher(None, left, right, autojunk=False).get_grouped_opcodes(0):
        i, j, a, b = ops[0][1], ops[-1][2], ops[0][3], ops[-1][4]
        # A newly added file may contain several functions. Split on declarations
        # so one commit/file can describe independent capabilities.
        chunks = [(i, j, a, b)]
        if i == j and b - a > 1:
            boundaries = [n for n in range(a + 1, b) if _DECL.search(right[n])]
            points = [a, *boundaries, b]
            chunks = [(i, j, x, y) for x, y in zip(points, points[1:])]
        for i, j, a, b in chunks:
            if len(units) >= MAX_UNITS:
                truncated = True
                break
            removed, added = "\n".join(left[i:j]), "\n".join(right[a:b])
            units.append({"symbol": _symbol(right, a) or _symbol(left, i), "kind": kind,
                          "before": safe_text(removed, 600), "after": safe_text(added, 600),
                          "context_before": _context(left, i), "context_after": _context(right, a),
                          "layer": layer, "basis": "source_diff",
                          "truncated": len(removed) > 600 or len(added) > 600})
    snapshot = {"before": before, "after": after} if max(len(before), len(after)) <= MAX_SNAPSHOT else None
    return {"units": units, "snapshot": snapshot, "units_truncated": truncated}


def observed_delta(old: dict, new: dict, path: str) -> dict:
    """Compare saved content, never reread today's files for yesterday's report."""
    old_snap, new_snap = old.get("snapshot"), new.get("snapshot")
    if old_snap and new_snap:
        result = capture(old_snap["after"], new_snap["after"], path)
        old_index = old_snap.get("index", old_snap["before"])
        new_index = new_snap.get("index", new_snap["before"])
        if old_index != new_index and new_index != new_snap["after"]:
            result["units"].extend(capture(old_index, new_index, path, layer="staged")["units"])
        for unit in result["units"]:
            unit["basis"] = "observation_delta"
        return dict(new, units=result["units"], units_truncated=result["units_truncated"],
                    before_hash=old.get("content_hash"), before_index_hash=old.get("index_hash"))
    if old:
        known = {json.dumps(u, sort_keys=True) for u in old.get("units", [])}
        return dict(new, before_hash=old.get("content_hash"), before_index_hash=old.get("index_hash"),
                    units=[dict(u, basis="snapshot_only") for u in new.get("units", [])
                                if json.dumps(u, sort_keys=True) not in known], delta_incomplete=True)
    return new


def fit_evidence(row: dict, before: str, after: str, remaining: int, *, index=None, layer="working") -> int:
    """Patch + contexts + snapshots share the existing per-scan text budget."""
    captured = capture(before, after, row["path"], layer=layer)
    if index is not None and index not in {before, after}:
        captured["units"].extend(capture(before, index, row["path"], layer="staged")["units"])
    captured["units_truncated"] |= any(u["truncated"] for u in captured["units"])
    if captured["snapshot"] is not None and index is not None:
        clean_index = safe_text(index, 512 * 1024)
        if len(clean_index) <= MAX_SNAPSHOT:
            captured["snapshot"]["index"] = clean_index
        else:
            captured["snapshot"] = None
    # Keep units first; incomplete snapshots never masquerade as full versions.
    snapshot = captured.pop("snapshot")
    size = len(json.dumps(captured, ensure_ascii=False))
    if size > remaining:
        row.update(units=[], units_truncated=True)
        return remaining
    row.update(captured)
    remaining -= size
    snapshot_size = len(json.dumps(snapshot, ensure_ascii=False)) if snapshot else 0
    if snapshot and snapshot_size <= remaining:
        row["snapshot"] = snapshot
        remaining -= snapshot_size
    return remaining
