"""Bounded, read-only source evidence. Deny before reading any file or blob."""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import stat
from pathlib import Path, PurePosixPath

from xiaomao.policy import path_is_denied, redact_secret_spans

TEXT_SUFFIXES = frozenset((
    ".py", ".go", ".rs", ".js", ".jsx", ".ts", ".tsx", ".swift", ".c", ".h",
    ".cpp", ".hpp", ".java", ".kt", ".rb", ".sh", ".sql", ".html", ".css",
    ".scss", ".md", ".rst", ".txt", ".vue", ".svelte",
))
SKIP_PARTS = frozenset((".git", ".venv", "node_modules", "vendor", "dist", "build", "__pycache__"))
MAX_FILE_BYTES = 256 * 1024
MAX_NEW_BYTES = 32 * 1024
MAX_FILES = 200
MAX_PATCH_CHARS = 2000
MAX_TOTAL_CHARS = 64 * 1024
_ASSIGNMENT = re.compile(
    r"(?im)^.*\b(?:api[_-]?key|secret|token|password|passwd|credential)\b"
    r"""["']?\s*[:=].*$"""
)


def safe_text(value: str, limit: int = MAX_PATCH_CHARS) -> str:
    if re.search(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----", value):
        return "[redacted: key material]"
    text = _ASSIGNMENT.sub("[redacted: sensitive assignment]", redact_secret_spans(value))
    text = re.sub(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s/@]+:[^\s/@]+@", "[redacted]@", text)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [redacted]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted]", text)
    text = "".join(c if c in "\n\t" or ord(c) >= 32 else " " for c in text)
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def readable_source(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not parts or path.startswith("/") or ".." in parts or path_is_denied(path):
        return False
    if any(p.lower() in SKIP_PARTS for p in parts):
        return False
    # Runtime configuration/credentials are metadata only, even if text.
    name = parts[-1].lower()
    if any(word in name for word in ("credential", "secret", "password", "config.", "settings.")):
        return False
    return Path(path).suffix.lower() in TEXT_SUFFIXES


def _decode(data: bytes) -> tuple[str | None, str]:
    if b"\0" in data:
        return None, "binary"
    try:
        return data.decode("utf-8"), "ok"
    except UnicodeDecodeError:
        return None, "binary"


def file_text(repo: Path, rel: str, limit: int) -> tuple[str | None, str]:
    path = repo / rel
    try:
        current = repo
        for part in PurePosixPath(rel).parts:
            current = current / part
            if current.is_symlink():
                return None, "symlink"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return None, "not_regular"
            if info.st_size > limit:
                return None, "oversize"
            data = stream.read(limit + 1)
        if len(data) > limit:
            return None, "oversize"
        return _decode(data)
    except FileNotFoundError:
        return "", "absent"
    except OSError:
        return None, "unreadable"


def blob_text(repo: Path, ref: str | None, rel: str) -> tuple[str | None, str]:
    from xiaomao.git_readonly import run_git

    if ref is None:
        return "", "absent"
    obj = f"{ref}:{rel}"
    size = run_git(repo, "cat-file", "-s", obj, check=False)
    if size.returncode:
        return "", "absent"
    if int(size.stdout.strip()) > MAX_FILE_BYTES:
        return None, "oversize"
    # Raw cat-file does not run textconv, external diff or checkout filters.
    raw = run_git(repo, "cat-file", "blob", obj, check=False)
    if raw.returncode:
        return None, "unreadable"
    if "\0" in raw.stdout or "\ufffd" in raw.stdout:
        return None, "binary"
    return raw.stdout, "ok"


def digest(text: str | None) -> str | None:
    return hashlib.sha256(text.encode()).hexdigest() if text is not None else None


def patch_text(before: str, after: str, rel: str) -> str:
    # Redact complete sources before context selection: a diff can begin in
    # the middle of a multiline credential, without the identifying header.
    before = safe_text(before, MAX_FILE_BYTES * 2)
    after = safe_text(after, MAX_FILE_BYTES * 2)
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"before/{rel}", tofile=f"after/{rel}", n=2,
    )
    # Cap while iterating, so large diffs do not inflate the in-memory packet.
    parts, size = [], 0
    for line in lines:
        parts.append(line)
        size += len(line)
        if size > MAX_PATCH_CHARS:
            break
    return safe_text("".join(parts))


def workspace_evidence(repo: Path, entries: list, head: str | None) -> tuple[list[dict], list[str]]:
    changes, limitations = [], []
    remaining = MAX_TOTAL_CHARS
    for entry in entries[:MAX_FILES]:
        rel = entry.path
        item = {"path": rel, "status": entry.staged + entry.unstaged}
        changes.append(item)
        if not readable_source(rel):
            item.update(availability="metadata_only", patch="")
            limitations.append(f"{rel}: metadata_only")
            continue
        text, state = file_text(repo, rel, MAX_NEW_BYTES if entry.is_untracked else MAX_FILE_BYTES)
        if state not in {"ok", "absent"}:
            item.update(availability=state, patch="")
            limitations.append(f"{rel}: {state}")
            continue
        before, before_state = blob_text(repo, head, rel)
        index, index_state = ("", "absent") if entry.is_untracked else blob_text(repo, "", rel)
        item.update(content_hash=digest(text), index_hash=digest(index), availability=state)
        snippets = []
        if before is not None and text is not None:
            snippets.append(patch_text(before, text, rel))
        if index is not None and before is not None and index not in {before, text}:
            snippets.append("暂存区：\n" + patch_text(before, index, rel))
        excerpt = "\n".join(part for part in snippets if part)
        item["patch"] = excerpt[:remaining]
        remaining -= len(item["patch"])
        if len(excerpt) > len(item["patch"]) or "[truncated]" in excerpt:
            limitations.append(f"{rel}: diff_truncated")
        for reason in (before_state, index_state):
            if reason not in {"ok", "absent"}:
                limitations.append(f"{rel}: {reason}")
    if len(entries) > MAX_FILES:
        limitations.append(f"file_limit: {len(entries) - MAX_FILES} additional paths")
    return changes, sorted(set(limitations))


def commit_evidence(repo: Path, oid: str) -> dict:
    from xiaomao.git_readonly import run_git

    raw = run_git(repo, "show", "-s", "--format=%H%x00%P%x00%an%x00%aI%x00%cI%x00%s", oid).stdout
    sha, parents, author, authored_at, committed_at, subject = raw.rstrip("\n").split("\0", 5)
    parent = parents.split()[0] if parents else None
    if parent:
        args = ("diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", parent, sha)
    else:
        args = ("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", sha)
    paths = [p for p in run_git(repo, *args).stdout.split("\0") if p]
    files, limitations, remaining = [], [], MAX_TOTAL_CHARS
    for rel in paths[:MAX_FILES]:
        row = {"path": rel, "patch": "", "availability": "metadata_only"}
        files.append(row)
        if not readable_source(rel):
            limitations.append(f"{rel}: metadata_only")
            continue
        before, bs = blob_text(repo, parent, rel)
        after, als = blob_text(repo, sha, rel)
        if before is None or after is None:
            row["availability"] = f"{bs}/{als}"
            limitations.append(f"{rel}: {bs}/{als}")
            continue
        row.update(availability="ok", content_hash=digest(after))
        excerpt = patch_text(before, after, rel)
        row["patch"] = excerpt[:remaining]
        remaining -= len(row["patch"])
        if len(excerpt) > len(row["patch"]) or "[truncated]" in excerpt:
            limitations.append(f"{rel}: diff_truncated")
    if len(paths) > MAX_FILES:
        limitations.append(f"file_limit: {len(paths) - MAX_FILES} additional paths")
    return {
        "sha": sha, "parents": parents.split(), "author": safe_text(author, 200),
        "authored_at": authored_at, "committed_at": committed_at,
        "subject": safe_text(subject, 500), "kind": "merge" if len(parents.split()) > 1 else "commit",
        "files": files, "path_count": len(paths), "limitations": limitations,
    }
