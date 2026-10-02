"""Explicit document handoffs. No inference, repository scan, or source execution."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from xiaomao.change_evidence import safe_text
from xiaomao.config import load_config
from xiaomao.paths import STORAGE_BUDGET_BYTES
from xiaomao.policy import dir_size_bytes, path_is_denied
from xiaomao.scope import path_exclusion_reason, project_exclusion_reason, worktree_exclusion_reason
from xiaomao.store import open_db, utc_now

MAX_DOCUMENT_BYTES = 256 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    project_id TEXT,
    scope_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    latest_revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS document_revisions (
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    revision INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('document','handoff')),
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    completeness TEXT NOT NULL,
    redacted INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    request_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    PRIMARY KEY(document_id, revision)
);
CREATE INDEX IF NOT EXISTS documents_scope_updated ON documents(scope_key, updated_at);
"""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: str, label: str, limit: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or "\0" in value or (not empty and not value.strip()):
        raise ValueError(f"{label}需要有效文本")
    if len(value.encode("utf-8")) > limit:
        raise ValueError(f"{label}超过 {limit} 字节上限；请拆分文档")
    return safe_text(value, limit=limit)


def _integer(value: int, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label}需要 {low}–{high} 之间的整数")
    return value


def read_document_file(path: Path) -> str:
    """Explicit CLI import only; MCP accepts supplied text, never arbitrary paths."""
    path = Path(path).expanduser().absolute()
    if (path.suffix.lower() not in {".md", ".markdown", ".txt", ".rst"}
            or ".git" in path.parts or path_is_denied(str(path))
            or path_exclusion_reason(str(path)) or path.is_symlink()):
        raise ValueError("只接受允许范围内的普通 Markdown / 文本文档")
    canonical = path.resolve(strict=True)
    for parent in canonical.parents:
        if (parent / ".git").exists() or (parent / ".git").is_symlink():
            if worktree_exclusion_reason(str(parent)):
                raise ValueError("文档来源仓库在排除范围")
            break
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DOCUMENT_BYTES:
            raise ValueError("文档不是普通文件或超过读取上限")
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    if path.resolve(strict=True) != canonical or len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("读取过程中来源变化或文档超限")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("文档必须是 UTF-8 文本") from None
    if "\0" in text:
        raise ValueError("拒绝二进制文档")
    return text


class DocumentStore:
    def __init__(self, home: Path, *, allowed_projects: tuple[str, ...] | None = None):
        self.home = Path(home).expanduser().resolve()
        self.db = self.home / "xiaomao.sqlite"
        self.allowed_projects = allowed_projects

    def _paths(self):
        for path in (self.db, self.home / "config.json", Path(str(self.db) + "-wal"),
                     Path(str(self.db) + "-shm")):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("小猫数据文件不能是链接或特殊文件")

    def _scope(self, project_id: str | None) -> str:
        self._paths()
        if self.allowed_projects is not None and project_id not in self.allowed_projects:
            raise ValueError("项目不在此文档入口的范围内")
        if project_id is None:
            return "personal-documents-v1"
        try:
            project = load_config(self.home).project(project_id)
        except KeyError:
            raise ValueError("项目未登记或已撤销；不能读取或保存其文档") from None
        if project_exclusion_reason(project):
            raise ValueError("项目在排除范围")
        root = str(Path(project.approved_root).expanduser().resolve())
        return _hash(json.dumps([project_id, root], ensure_ascii=False))

    def _allowed(self, row) -> bool:
        try:
            return self._scope(row["project_id"]) == row["scope_key"]
        except ValueError:
            return False

    @contextmanager
    def _reader(self):
        self._paths()
        if not self.db.exists():
            yield None
            return
        with closing(sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            yield conn if {"documents", "document_revisions"} <= tables else None

    def _document(self, conn, document_id):
        if not isinstance(document_id, str) or not re.fullmatch(r"doc_[a-f0-9]{32}", document_id):
            raise ValueError("无效文档编号")
        row = conn.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone() if conn else None
        if row is None or not self._allowed(row):
            raise ValueError("文档不存在或当前范围不允许读取")
        return row

    def _receipt(self, row):
        return {"saved": True, "document_id": row["document_id"], "revision": row["revision"],
                "kind": row["kind"], "content_hash": row["content_hash"], "source": row["source"],
                "completeness": row["completeness"], "redacted": bool(row["redacted"]),
                "saved_at": row["created_at"]}

    def _save(self, *, content, source, source_ref, completeness, request_id,
              title=None, project_id=None, document_id=None, expected_revision=None):
        original = (content, title, source_ref)
        content = _text(content, "正文", MAX_DOCUMENT_BYTES)
        source_ref = _text(source_ref, "来源引用", 2048, empty=True)
        if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", source):
            raise ValueError("来源名称需要 1–40 个字母、数字或 ._- 字符")
        if safe_text(source, limit=40) != source:
            raise ValueError("来源标签包含疑似敏感内容")
        if completeness not in {"full", "excerpt", "agent_summary"}:
            raise ValueError("完整性必须为 full、excerpt 或 agent_summary")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128 or "\0" in request_id:
            raise ValueError("需要 1–128 字符的稳定请求编号")
        if document_id is None:
            title = _text(title, "标题", 512).replace("\n", " ").replace("\r", " ")
            self._scope(project_id)
        else:
            _integer(expected_revision, "预期版本", 1, 2**31 - 1)
            with self._reader() as conn:
                self._document(conn, document_id)
        redacted = original != (content, title, source_ref)
        payload = dict(content=content, source=source, source_ref=source_ref, completeness=completeness,
                       title=title, project_id=project_id, document_id=document_id, expected_revision=expected_revision)
        request_hash = _hash(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        request_key = _hash(source + "\0" + request_id)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._paths()
        with open_db(self.db) as conn:
            os.chmod(self.db, 0o600)
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT document_id,revision,request_hash FROM document_revisions WHERE request_key=?", (request_key,)).fetchone()
            if existing:
                self._document(conn, existing["document_id"])
                if existing["request_hash"] != request_hash:
                    raise ValueError("同一请求编号提交了不同内容；请使用新编号")
                existing = conn.execute("SELECT * FROM document_revisions WHERE request_key=?", (request_key,)).fetchone()
                return self._receipt(existing)
            if dir_size_bytes(self.home) + len(json.dumps(payload).encode()) + 16384 > STORAGE_BUDGET_BYTES:
                raise ValueError("小猫存储预算不足；文档尚未保存，历史内容保留")
            now = utc_now()
            if document_id is None:
                scope = self._scope(project_id)
                document_id = "doc_" + uuid.uuid4().hex
                revision, kind = 1, "document"
                conn.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)",
                             (document_id, title, project_id, scope, now, now, revision))
            else:
                current = self._document(conn, document_id)
                if current["latest_revision"] != expected_revision:
                    raise ValueError("文档版本已变化；请读取最新交接后再追加")
                revision, kind = expected_revision + 1, "handoff"
                conn.execute("UPDATE documents SET latest_revision=?,updated_at=? WHERE document_id=?",
                             (revision, now, document_id))
            conn.execute("INSERT INTO document_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (document_id, revision, kind, content, _hash(content), source, source_ref,
                          completeness, int(redacted), now, request_key, request_hash))
            row = conn.execute("SELECT * FROM document_revisions WHERE document_id=? AND revision=?",
                               (document_id, revision)).fetchone()
            receipt = self._receipt(row)
        return receipt

    def save(self, *, title, content, source, request_id, project_id=None, source_ref="", completeness="full"):
        return self._save(title=title, content=content, source=source, request_id=request_id,
                          project_id=project_id, source_ref=source_ref, completeness=completeness)

    def append(self, document_id, *, content, source, expected_revision, request_id,
               source_ref="", completeness="agent_summary"):
        return self._save(document_id=document_id, content=content, source=source, request_id=request_id,
                          expected_revision=expected_revision, source_ref=source_ref, completeness=completeness)

    def list(self, *, project_id=None, query="", limit=20, offset=0):
        _integer(limit, "条数", 1, 100)
        _integer(offset, "偏移", 0, 1000000)
        query = _text(query, "关键词", 512, empty=True)
        if project_id is not None:
            self._scope(project_id)
        found = []
        with self._reader() as conn:
            if conn is not None:
                # Check current scope before loading any saved source text.
                for row in conn.execute("SELECT * FROM documents ORDER BY updated_at DESC, document_id DESC"):
                    if (project_id is not None and row["project_id"] != project_id) or not self._allowed(row):
                        continue
                    if query and query.casefold() not in row["title"].casefold():
                        match = conn.execute("SELECT 1 FROM document_revisions WHERE document_id=? AND instr(lower(content), lower(?))>0 LIMIT 1",
                                             (row["document_id"], query)).fetchone()
                        if not match:
                            continue
                    entry = dict(row)
                    entry.pop("scope_key")
                    found.append(entry)
        page = found[offset:offset + limit]
        return {"documents": page, "total": len(found),
                "next_offset": offset + limit if offset + limit < len(found) else None}

    def read(self, document_id, *, revision=None, offset=0, max_chars=12000):
        _integer(offset, "正文偏移", 0, MAX_DOCUMENT_BYTES)
        _integer(max_chars, "正文长度", 1, 32000)
        with self._reader() as conn:
            doc = self._document(conn, document_id)
            version = doc["latest_revision"] if revision is None else _integer(revision, "版本", 1, 2**31 - 1)
            row = conn.execute("SELECT * FROM document_revisions WHERE document_id=? AND revision=?", (document_id, version)).fetchone()
            if row is None:
                raise ValueError("该文档版本不存在")
            content = row["content"]
            if offset > len(content):
                raise ValueError("正文偏移超出文档")
            if _hash(content) != row["content_hash"]:
                raise ValueError("文档正文校验失败")
            result = self._receipt(row)
            result.pop("saved")
            result.update(title=doc["title"], project_id=doc["project_id"], latest_revision=doc["latest_revision"],
                          source_ref=row["source_ref"], content=content[offset:offset + max_chars], offset=offset,
                          total_chars=len(content), next_offset=offset + max_chars if offset + max_chars < len(content) else None,
                          original_revision=1, provenance="来源自述；保存不代表测试、发布或任务完成已验证")
            return result

    def history(self, document_id, *, offset=0, limit=20):
        _integer(offset, "历史偏移", 0, 1000000)
        _integer(limit, "条数", 1, 100)
        with self._reader() as conn:
            doc = self._document(conn, document_id)
            rows = conn.execute("SELECT revision,kind,source,source_ref,completeness,redacted,created_at,content_hash FROM document_revisions WHERE document_id=? ORDER BY revision DESC LIMIT ? OFFSET ?",
                                (document_id, limit, offset)).fetchall()
            return {"document_id": document_id, "latest_revision": doc["latest_revision"],
                    "revisions": [dict(r) for r in rows],
                    "next_offset": offset + limit if offset + limit < doc["latest_revision"] else None}

    def export(self, document_id):
        with self._reader() as conn:
            doc = self._document(conn, document_id)
            heading = (f"# {doc['title']}\n\n文档编号：{document_id}\n\n"
                       f"项目：{doc['project_id'] or '个人资料'}\n\n截至版本：{doc['latest_revision']}\n\n"
                       "以下保留各次来源自述。保存不代表测试、发布或任务完成已验证。\n")
            folders = (self.home / "reports", self.home / "reports" / "documents")
            for folder in folders:
                if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
                    raise ValueError("文档导出目录不能是链接或特殊文件")
                folder.mkdir(mode=0o700, exist_ok=True)
            folder = folders[-1]
            dest = folder / f"{document_id}-v{doc['latest_revision']}.md"
            if dest.is_symlink():
                raise ValueError("导出目标不能是链接")
            estimated = conn.execute("SELECT coalesce(sum(length(CAST(content AS BLOB))),0)+count(*)*4096 FROM document_revisions WHERE document_id=?", (document_id,)).fetchone()[0]
            if dir_size_bytes(self.home) + estimated + 8192 > STORAGE_BUDGET_BYTES:
                raise ValueError("导出超出存储预算；已保存的文档仍可分页读取")
            fd, name = tempfile.mkstemp(prefix=".document-", dir=folder)
            temp = Path(name)
            digest = hashlib.sha256()
            try:
                with os.fdopen(fd, "wb") as stream:
                    def write(value):
                        data = value.encode("utf-8")
                        stream.write(data)
                        digest.update(data)
                    write(heading)
                    for row in conn.execute("SELECT * FROM document_revisions WHERE document_id=? ORDER BY revision", (document_id,)):
                        if _hash(row["content"]) != row["content_hash"]:
                            raise ValueError("文档正文校验失败")
                        write(f"\n---\n\n## 版本 {row['revision']} · {'原始文档' if row['kind']=='document' else '追加交接'}\n\n")
                        completeness = {"full": "提交全文", "excerpt": "原文片段", "agent_summary": "Agent 交接摘要"}[row["completeness"]]
                        filtering = "已过滤敏感片段" if row["redacted"] else "未触发敏感内容过滤"
                        write(f"来源：{row['source']}；保存时间：{row['created_at']}\n\n")
                        write(f"保存内容：{completeness}；{filtering}\n\n")
                        if row["source_ref"]:
                            write(f"来源引用：{row['source_ref']}\n\n")
                        write(row["content"] + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temp, dest)  # Publish without clobbering any existing file.
                except FileExistsError:
                    if dest.is_symlink() or not dest.is_file():
                        raise ValueError("导出目标不安全") from None
                    existing = hashlib.sha256()
                    with dest.open("rb") as stream:
                        for block in iter(lambda: stream.read(65536), b""):
                            existing.update(block)
                    if existing.hexdigest() != digest.hexdigest():
                        raise ValueError("现有导出文件已变化；保留该文件，请先核对") from None
            finally:
                temp.unlink(missing_ok=True)
            return {"document_id": document_id, "revision": doc["latest_revision"],
                    "path": str(dest), "sha256": digest.hexdigest()}
