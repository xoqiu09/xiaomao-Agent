"""Versioned, source-attributed project context and explicit user corrections.

Read only authorized repositories/cache. Context describes a project; it is
never daily implementation evidence. All model-facing data is sanitized.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path

from xiaomao.change_evidence import blob_text, file_text, safe_text
from xiaomao.inventory import scope_key
from xiaomao.scope import project_exclusion_reason

SCHEMA = """
CREATE TABLE IF NOT EXISTS feature_catalogs (
  version TEXT NOT NULL, scope_key TEXT NOT NULL, project_id TEXT NOT NULL,
  at_utc TEXT NOT NULL, payload_json TEXT NOT NULL,
  PRIMARY KEY(version, scope_key, at_utc)
);
"""
VERSION = "feature-context-v1"
_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def label(value, maximum=100):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or safe_text(value, maximum) != value or any(c in value for c in "\n\r|")):
        raise ValueError("功能目录包含无效文字")
    return value.strip()


def validate_catalog(payload):
    if not isinstance(payload, dict):
        raise ValueError("功能目录应为 JSON 对象")
    purpose = label(payload.get("purpose", ""), 200)
    rows = payload.get("features")
    if not isinstance(rows, list) or len(rows) > 40:
        raise ValueError("功能目录最多 40 项")
    features, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not _ID.fullmatch(row.get("id", "")) or row["id"] in seen:
            raise ValueError("功能 ID 无效或重复")
        seen.add(row["id"])
        feature = {"id": row["id"], "name": label(row.get("name")), "priority": row.get("priority", 2)}
        if re.search(r"[/\\]|\.(?:py|go|ts|js|md|rs)\b", feature["name"]):
            raise ValueError("功能名称应为可读名称，路径写在匹配条件内")
        if feature["priority"] not in (1, 2, 3):
            raise ValueError("功能优先级须为 1、2 或 3")
        for key in ("paths", "symbols"):
            values = row.get(key, [])
            if not isinstance(values, list) or len(values) > 30:
                raise ValueError("功能匹配条件超限")
            feature[key] = [label(v, 160) for v in values]
            if any(v.startswith("/") or ".." in Path(v).parts for v in feature[key]):
                raise ValueError("功能匹配条件须位于项目内")
        if not feature["paths"] and not feature["symbols"]:
            raise ValueError("每项功能至少提供路径或符号匹配条件")
        features.append(feature)
    return {"purpose": purpose, "features": features}


def save_catalog(conn, cfg, project_id, payload, *, at=None):
    from xiaomao.store import utc_now
    from xiaomao.activity import stamp
    project = cfg.project(project_id)
    if project_exclusion_reason(project):
        raise ValueError("项目在排除范围")
    payload = validate_catalog(payload)
    version, at = fingerprint(payload), stamp(at or utc_now())
    conn.execute("INSERT OR IGNORE INTO feature_catalogs VALUES (?,?,?,?,?)",
                 (version, scope_key(project), project_id, at, json.dumps(payload, ensure_ascii=False)))
    return {"version": version, "effective_from": at, **payload}


def catalog_at(conn, project, at):
    row = conn.execute("SELECT * FROM feature_catalogs WHERE scope_key=? AND at_utc<=? "
                       "ORDER BY at_utc DESC,rowid DESC LIMIT 1", (scope_key(project), at)).fetchone()
    if not row:
        return None
    return {"version": row["version"], "at": row["at_utc"], **json.loads(row["payload_json"])}


def _briefing(cfg, project_id, at):
    from xiaomao.briefing import cache_paths, load_cached_understanding
    from xiaomao.handoff_view import _read_file
    from xiaomao.activity import stamp
    body, meta = cache_paths(cfg, project_id)
    try:
        if body.parent.resolve() != Path(cfg.home).resolve() / "reports/briefing":
            return None
        # Bound and reject links before calling the existing validated cache reader.
        _read_file(body, body.parent, 32 * 1024)
        _read_file(meta, meta.parent, 4096)
        cached = load_cached_understanding(cfg, project_id)
        if cached and stamp(cached["generated_at"]) <= at:
            understanding = cached["understanding"]
            return {"kind": "briefing_cache", "source_hash": cached["source_sha256"],
                    "purpose": safe_text(understanding.get("purpose", ""), 200),
                    "excerpt": safe_text(json.dumps({k: understanding.get(k) for k in
                               ("call_chain", "invariants", "do_not_claim")}, ensure_ascii=False), 1500)}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def snapshot_context(conn, cfg, project, path, at, *, ref=None):
    from xiaomao.ops import storage_over_budget
    if storage_over_budget(Path(cfg.home)):
        return {"schema": VERSION, "version": "unavailable", "purpose": "", "features": [],
                "sources": [], "limitations": ["project_context_storage_budget"]}
    sources, features, limitations = [], [], []
    # Fixed documented source; never enumerate the mixed/company briefing root.
    readme, status = blob_text(path, ref, "README.md") if ref else file_text(path, "README.md", 32 * 1024)
    if readme and status == "ok":
        excerpt = safe_text(readme, 3500)
        sources.append({"kind": "readme", "source_hash": fingerprint(excerpt),
                        "ref": ref or "observed_workspace", "excerpt": excerpt})
        # Explicit doc mappings become suggestions; documentation never proves implementation.
        heading = ""
        for line in excerpt.splitlines():
            if line.startswith("## "):
                heading = line[3:].strip()[:80]
            paths = re.findall(r"`([\w./*-]+\.(?:py|ts|tsx|js|go|rs|swift))`", line)
            cells = [s.strip().strip("`*") for s in line.split("|") if s.strip()]
            name = cells[0] if len(cells) > 1 else heading
            if paths and name and not re.search(r"[/\\]|\.(py|ts|go)\b", name) and len(features) < 30:
                features.append({"id": "doc-" + fingerprint([name, paths])[:12], "name": name,
                                 "paths": paths, "symbols": [], "priority": 2,
                                 "source": sources[0]["source_hash"]})
    else:
        limitations.append("project_readme_unavailable")
    briefing = _briefing(cfg, project.project_id, at)
    if briefing:
        sources.append(briefing)
    correction = catalog_at(conn, project, at)
    purpose = briefing.get("purpose", "") if briefing else ""
    if not purpose and readme:
        purpose = next((safe_text(line.strip(), 200) for line in readme.splitlines()
                        if line.strip() and not line.startswith(("#", "|", "```"))), "")
    if correction:
        purpose, features = correction["purpose"], correction["features"]
        sources.append({"kind": "user_correction", "source_hash": correction["version"],
                        "effective_from": correction["at"]})
    payload = {"schema": VERSION, "purpose": purpose, "features": features,
               "sources": sources, "limitations": limitations,
               "usage": "static_background_only"}
    return dict(payload, version=fingerprint(payload))


def match_features(context, path, symbol):
    features = context.get("features", [])
    exact = [f for f in features if symbol and symbol in f.get("symbols", [])]
    return exact or [f for f in features if any(fnmatch.fnmatchcase(path, p) for p in f.get("paths", []))]
