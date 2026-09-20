"""Consistent SQLite backup / runtime migration.

Never copy a live WAL database by file. Use the SQLite backup API so the
destination is a checkpointed snapshot. The source tree is left in place as
the recoverable copy.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xiaomao.paths import layout


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1"
        )
    ]
    out: dict[str, int] = {}
    for name in names:
        if name.endswith("_fts") or name.startswith("evidence_fts"):
            continue
        out[name] = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    return out


def backup_sqlite(src: Path, dest: Path) -> dict[str, Any]:
    """Write a consistent copy of `src` to `dest` via sqlite3.Connection.backup."""
    src = Path(src)
    dest = Path(dest)
    if not src.exists():
        raise FileNotFoundError(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(f"refusing to overwrite existing backup: {dest}")

    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        src_conn.execute("PRAGMA query_only=ON")
        integrity = src_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"source integrity_check={integrity}")
        counts = _table_counts(src_conn)
        dest_conn = sqlite3.connect(str(dest), timeout=30)
        try:
            src_conn.backup(dest_conn)
            dest_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dest_integrity = dest_conn.execute("PRAGMA integrity_check").fetchone()[0]
            dest_counts = _table_counts(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()

    if dest_integrity != "ok":
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"backup integrity_check={dest_integrity}")
    if dest_counts != counts:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"backup row counts differ: src={counts} dest={dest_counts}")
    os.chmod(dest, 0o600)
    return {
        "src": str(src),
        "dest": str(dest),
        "integrity": dest_integrity,
        "counts": dest_counts,
        "bytes": dest.stat().st_size,
    }


def migrate_runtime(src_home: Path, dest_home: Path, *, archive_dir: Path) -> dict[str, Any]:
    """Copy sqlite + keep a recoverable archive. Does not delete src_home."""
    src_home = Path(src_home)
    dest_home = Path(dest_home)
    src_db = layout(src_home)["db"]
    dest_db = layout(dest_home)["db"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_db = Path(archive_dir) / f"runtime-{stamp}" / "xiaomao.sqlite"

    archive = backup_sqlite(src_db, archive_db)
    if dest_db.exists():
        raise FileExistsError(f"destination already has a database: {dest_db}")
    dest_home.mkdir(parents=True, exist_ok=True)
    installed = backup_sqlite(src_db, dest_db)

    # Best-effort copy of empty layout dirs already handled by ensure_layout.
    copied_sidecars = []
    for name in ("evidence", "reports", "logs", "cache"):
        src_dir = src_home / name
        dest_dir = dest_home / name
        if not src_dir.is_dir():
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        for item in src_dir.rglob("*"):
            rel = item.relative_to(src_dir)
            target = dest_dir / rel
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif item.is_file() and item.name != ".keep":
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copy2(item, target)
                    copied_sidecars.append(str(rel))

    return {
        "src_home": str(src_home),
        "dest_home": str(dest_home),
        "archive": archive,
        "installed": installed,
        "copied_sidecars": copied_sidecars,
        "src_retained": True,
    }


def _sha256_file(path: Path, bufsize: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(bufsize)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def inventory_models_dir(root: Path, *, hash_blobs: bool = True) -> dict[str, Any]:
    """Record manifests and blobs. Does not mutate the tree."""
    root = Path(root)
    manifests: list[dict[str, Any]] = []
    man_root = root / "manifests"
    if man_root.is_dir():
        for path in sorted(p for p in man_root.rglob("*") if p.is_file()):
            manifests.append(
                {
                    "rel": str(path.relative_to(root)),
                    "size": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
    blobs: list[dict[str, Any]] = []
    blob_root = root / "blobs"
    if blob_root.is_dir():
        for path in sorted(p for p in blob_root.iterdir() if p.is_file()):
            row: dict[str, Any] = {"name": path.name, "size": path.stat().st_size}
            if hash_blobs:
                row["sha256"] = _sha256_file(path)
            blobs.append(row)
    return {
        "root": str(root),
        "exists": root.exists(),
        "manifests": manifests,
        "blobs": blobs,
        "manifest_count": len(manifests),
        "blob_count": len(blobs),
        "blob_bytes": sum(b["size"] for b in blobs),
    }


def migrate_models_dir(src: Path, dest: Path, *, hash_blobs: bool = True) -> dict[str, Any]:
    """Copy an Ollama models tree. Refuse unknown dest data. Keep src."""
    src = Path(src)
    dest = Path(dest)
    if not src.is_dir():
        raise FileNotFoundError(src)
    if dest.exists():
        leftover = [p.name for p in dest.iterdir() if p.name != ".DS_Store"]
        if leftover:
            raise FileExistsError(f"refusing to overwrite existing models dir: {dest} contains {leftover[:8]}")
    before = inventory_models_dir(src, hash_blobs=hash_blobs)
    if not before["manifests"]:
        raise RuntimeError(f"source has no manifests: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=False)
    after = inventory_models_dir(dest, hash_blobs=hash_blobs)
    if after["manifests"] != before["manifests"] or after["blobs"] != before["blobs"]:
        shutil.rmtree(dest)
        raise RuntimeError("models copy failed inventory check; destination removed")
    return {
        "src": str(src),
        "dest": str(dest),
        "src_retained": True,
        "before": before,
        "after": after,
        "restore": f"rm -rf {dest}  # source kept at {src}",
    }
