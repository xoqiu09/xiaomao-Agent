from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Iterable

# Denied by basename or suffix before any content is read.
DENIED_BASENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".env.test",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "credentials",
    "credentials.json",
    "service-account.json",
    "authorized_keys",
    "known_hosts",
    ".git-credentials",
}

DENIED_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".kdbx",
    ".ovpn",
)

DENIED_DIR_PARTS = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".gcloud",
    "keychain",
}

# Never ingest our own reports back into the observer.
SELF_OUTPUT_DIR_PARTS = {
    "Application Support/Xiaomao",
    "Application Support/LocalCat",
}

MAX_UNTRACKED_BYTES_TO_READ = 32 * 1024
MAX_EXCERPT_CHARS = 2000

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN CERTIFICATE-----"),
)


def normalize_relpath(path: str) -> str:
    rel = path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.lstrip("/")


def path_is_denied(relpath: str) -> bool:
    rel = normalize_relpath(relpath)
    name = Path(rel).name
    if name in DENIED_BASENAMES or name.startswith(".env."):
        return True
    lower = name.lower()
    if lower.endswith(DENIED_SUFFIXES):
        return True
    parts = {p.lower() for p in Path(rel).parts}
    if parts & DENIED_DIR_PARTS:
        return True
    return False


def is_self_output(path: Path) -> bool:
    text = str(path)
    return any(part in text for part in SELF_OUTPUT_DIR_PARTS)


def looks_like_secret(text: str) -> bool:
    if not text:
        return False
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            return True
    return False


def redact_text(text: str) -> str:
    if looks_like_secret(text):
        return "[redacted: potential secret]"
    if len(text) > MAX_EXCERPT_CHARS:
        return text[:MAX_EXCERPT_CHARS] + "\n…[truncated]"
    return text


def safe_excerpt_from_bytes(data: bytes) -> tuple[str | None, str]:
    """Return (excerpt_or_none, redaction_status). Never returns secret material."""
    if not data:
        return "", "clean"
    if b"\x00" in data[:4096]:
        return None, "binary"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            return None, "undecodable"
    if looks_like_secret(text):
        return None, "secret"
    return redact_text(text), "clean"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dir_size_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git"}]
        for name in filenames:
            fp = Path(dirpath) / name
            try:
                total += fp.stat().st_size
            except OSError:
                continue
    return total


def ensure_within_root(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def filter_untracked(entries: Iterable[str], *, max_count: int = 500) -> tuple[list[str], int]:
    items = list(entries)
    return items[:max_count], max(0, len(items) - max_count)
