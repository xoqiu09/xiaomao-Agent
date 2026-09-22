"""Inspect existing handoffs without scanning, inference, migrations or writes.

A sidecar binds the report to its observation IDs and collection cutoff. File
mtime is deliberately not evidence. Legacy reports remain unverified.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from xiaomao.config import ProjectSpec, load_config
from xiaomao.scope import project_exclusion_reason

STALE_AFTER_S = 660
UNKNOWN_ITEMS = [
    "当前版本测试是否通过：unknown（未执行测试、未接入测试报告）",
    "是否已部署 / 已验收：unknown（没有远程连接器）",
    "两次采集间撤销或未保存到磁盘的编辑：不可见",
]


def parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except ValueError:
        return None


def scope_identity(project: ProjectSpec) -> str:
    payload = {
        "project_id": project.project_id,
        "approved_root": str(Path(project.approved_root).resolve()),
        "worktrees": sorted((w.worktree_id, str(Path(w.path).resolve()), w.scan) for w in project.worktrees),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def snapshot(conn, project: ProjectSpec) -> dict:
    """Only query this project's metadata; never load evidence or report bodies."""
    observations = []
    for wt in project.worktrees:
        if not wt.scan:
            continue
        row = conn.execute(
            "SELECT observation_id, observed_at_utc, collection_status, json_extract(facts_json, '$.toplevel') FROM observations "
            "WHERE project_id=? AND worktree_id=? ORDER BY observed_at_utc DESC, rowid DESC LIMIT 1",
            (project.project_id, wt.worktree_id),
        ).fetchone()
        registered = conn.execute(
            "SELECT canonical_path FROM worktrees WHERE project_id=? AND worktree_id=?",
            (project.project_id, wt.worktree_id),
        ).fetchone()
        observations.append({
            "worktree_id": wt.worktree_id,
            "path": str(Path(wt.path).resolve()),
            "registered_path": registered[0] if registered else None,
            "observation_id": row[0] if row else None,
            "observed_at_utc": row[1] if row else None,
            "collection_status": row[2] if row else None,
            "observed_path": row[3] if row else None,
        })
    run = conn.execute(
        "SELECT run_id, finished_at, outcome FROM scan_runs WHERE project_id=? "
        "ORDER BY finished_at DESC, rowid DESC LIMIT 1", (project.project_id,),
    ).fetchone()
    scan = dict(zip(("run_id", "finished_at", "outcome"), run)) if run else None
    if scan:
        proof = conn.execute(
            "SELECT json_extract(payload_json, '$.scope_sha256'), json_extract(payload_json, '$.observation_ids') "
            "FROM events WHERE project_id=? AND kind=? AND json_extract(payload_json, '$.run_id')=? "
            "ORDER BY rowid DESC LIMIT 1", (project.project_id, "scan_" + scan["outcome"], scan["run_id"]),
        ).fetchone()
        scan["scope_sha256"] = proof[0] if proof else None
        scan["observation_ids"] = json.loads(proof[1]) if proof and proof[1] else None
    return {"observations": observations, "scan": scan}


def scan_covers_scope(scan: dict, observations: list[dict], project: ProjectSpec) -> bool:
    expected = [{"worktree_id": row["worktree_id"], "observation_id": row["observation_id"]} for row in observations]
    return (scan.get("scope_sha256") == scope_identity(project)
            and scan.get("observation_ids") == expected)


def metadata_for(conn, project: ProjectSpec, body: str, generated_at: str) -> dict:
    state = snapshot(conn, project)
    return {
        "schema_version": 1,
        "project_id": project.project_id,
        "generated_at_utc": generated_at,
        "scope_sha256": scope_identity(project),
        "report_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        **state,
    }


@dataclass
class HandoffView:
    project_id: str | None
    status: str = "未核实"
    reason: str = ""
    exit_code: int = 3
    path: Path | None = None
    body: str | None = None
    generated_at: str | None = None
    collected_through: str | None = None
    sources: list[str] = field(default_factory=list)

    def text(self, *, include_body: bool = False) -> str:
        lines = [f"最新交接：{self.project_id or '未选择项目'}", f"资料状态：{self.status}", self.reason]
        if self.path:
            lines.append(f"来源文件：{self.path}")
        lines.extend([f"生成时间（UTC）：{self.generated_at or 'unknown'}", f"采集核对截至（UTC）：{self.collected_through or 'unknown'}"])
        lines.extend(f"观察来源：{source}" for source in self.sources)
        lines.append("未核实项：")
        lines.extend(f"- {item}" for item in UNKNOWN_ITEMS)
        lines.append("查询只读；不会扫描、加载模型或更新资料时间。")
        if include_body and self.body is not None:
            lines.extend(["", "交接原文（以以上资料状态为准）：", self.body.rstrip()])
        return "\n".join(lines) + "\n"


def _read_file(path: Path, folder: Path, limit: int) -> bytes:
    # Do not follow a report/sidecar symlink to another project's private data.
    if path.is_symlink() or path.resolve().parent != folder.resolve() or not path.is_file():
        raise ValueError("report_path")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("report_size")
    return data


def inspect_handoff(home: Path, project_id: str | None, *, now: datetime | None = None) -> HandoffView:
    view = HandoffView(project_id)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        if not (home / "config.json").is_file():
            view.reason = "没有项目配置；未读取任何交接。"
            return view
        cfg = load_config(home)
        eligible = [p for p in cfg.projects if not project_exclusion_reason(p)]
        if project_id is None:
            if len(eligible) != 1:
                view.reason = "请用 --project 选择已授权项目：" + (", ".join(p.project_id for p in eligible) or "尚无")
                return view
            project_id = eligible[0].project_id
            view.project_id = project_id
        project = cfg.project(project_id)
        reason = project_exclusion_reason(project)
        if reason:
            view.status, view.reason = "范围已排除", reason
            return view
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", project_id):
            view.reason = "项目 ID 格式无效；未读取交接。"
            return view
        folder = home / "reports" / "handoff"
        if folder.resolve() != home.resolve() / "reports" / "handoff":
            view.reason = "报告目录越出数据 home；未读取交接。"
            return view
        pattern = re.compile(re.escape(project_id) + r"-\d{8}-\d{6}(?:-\d{6})?\.txt")
        files = sorted(p for p in folder.glob("*.txt") if pattern.fullmatch(p.name))
        if not files:
            view.status, view.reason, view.exit_code = "尚无资料", "没有找到该项目的交接；先采集，再生成 handoff。", 2
            return view
        view.path = files[-1]
        sidecar = view.path.with_suffix(".json")
        if not sidecar.exists():
            view.reason = "旧交接没有来源校验信息；不能证明资料新鲜，请重新生成 handoff。"
            return view
        meta = json.loads(_read_file(sidecar, folder, 128 * 1024))
        if not isinstance(meta, dict) or meta.get("schema_version") != 1 or meta.get("project_id") != project_id:
            raise ValueError("metadata_schema")
        if meta.get("scope_sha256") != scope_identity(project):
            view.reason = "交接生成后登记范围已变更；请重新采集并生成交接。"
            return view
        body_bytes = _read_file(view.path, folder, 2 * 1024 * 1024)
        if hashlib.sha256(body_bytes).hexdigest() != meta.get("report_sha256"):
            raise ValueError("report_hash")
        generated = parse_time(meta.get("generated_at_utc"))
        scan = meta.get("scan")
        observed = meta.get("observations")
        if not generated or not isinstance(observed, list):
            raise ValueError("metadata_time_or_observations")
        view.generated_at = generated.isoformat()
        if not observed or not isinstance(scan, dict):
            view.status, view.reason = "尚无资料", "没有完整的采集记录；生成了文件也不代表观察成功。"
            return view
        cutoff = parse_time(scan.get("finished_at"))
        if not cutoff or cutoff > generated or generated > now or cutoff > now:
            view.reason = "资料时间缺失、顺序错误或处于未来；请核对时钟并重新采集。"
            return view
        view.collected_through = cutoff.isoformat()
        expected = {w.worktree_id for w in project.worktrees if w.scan}
        if {row.get("worktree_id") for row in observed} != expected or len(observed) != len(expected):
            raise ValueError("metadata_worktrees")
        for row in observed:
            at = parse_time(row.get("observed_at_utc"))
            if not row.get("observation_id") or not at or at > cutoff or row.get("collection_status") != "ok":
                view.status, view.reason = "采集未完成", "至少一棵授权工作树缺失或采集失败；不能当成完整交接。"
                return view
            if (not row.get("registered_path") or not row.get("observed_path")
                    or str(Path(row["registered_path"]).resolve()) != row.get("path")
                    or str(Path(row["observed_path"]).resolve()) != row.get("path")):
                view.reason = "观察来源与当前登记路径不一致；请重新采集。"
                return view
            view.sources.append(f"{row['worktree_id']} / {row['observation_id']} / 首次记录 {at.isoformat()}")
        if scan.get("outcome") != "success":
            view.status, view.reason = "采集未完成", "生成交接时最近一次采集失败或跳过。"
            return view
        if not scan_covers_scope(scan, observed, project):
            view.reason = "最近扫描未证明覆盖当前启用范围；请按当前配置重新采集。"
            return view
        # mode=ro + query_only avoids schema setup and application data writes.
        db = home / "xiaomao.sqlite"
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            conn.execute("PRAGMA query_only=ON")
            current = snapshot(conn, project)
        if current["observations"] != observed:
            view.status, view.reason = "交接已落后", "交接后观察记录或来源已变化；请重新生成交接。"
            return view
        if not current["scan"] or current["scan"]["outcome"] != "success":
            view.status, view.reason = "采集未完成", "当前最近一次采集失败或跳过；不能回退到旧成功。"
            return view
        if not scan_covers_scope(current["scan"], current["observations"], project):
            view.reason = "当前扫描没有覆盖范围证明；不能借用其他工作树的成功时间。"
            return view
        current_time = parse_time(current["scan"]["finished_at"])
        if not current_time or current_time > now or current_time < cutoff:
            view.reason = "当前采集记录时间无效；无法核实资料时效。"
            return view
        view.body = body_bytes.decode("utf-8")
        if (now - cutoff).total_seconds() > STALE_AFTER_S:
            view.status, view.reason = "信息已过期", "交接引用的采集核对已超过 11 分钟；文件修改时间不代表资料更新。"
            return view
        view.status, view.reason, view.exit_code = "交接可读", "观察资料在有效期内；测试、部署及验收仍未核实。", 0
        return view
    except KeyError:
        view.reason = "项目未登记；未读取交接。"
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
        view.reason = "报告、配置或状态库不可读，或来源校验失败；未核实。"
    return view
