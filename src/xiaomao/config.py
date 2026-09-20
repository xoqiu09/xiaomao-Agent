from __future__ import annotations

import json
import os
import platform
import shutil
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xiaomao.paths import (
    DEFAULT_EXTERNAL_DATA,
    DEFAULT_EXTERNAL_MOUNT,
    DEFAULT_EXTERNAL_UUID,
    DEFAULT_MODELS_DIR,
    POLICY_VERSION,
    default_home,
    layout,
)

SCHEMA_VERSION = 1


@dataclass
class WorktreeSpec:
    worktree_id: str
    path: str
    scan: bool = True
    notes: str = ""


@dataclass
class ProjectSpec:
    project_id: str
    display_name: str
    approved_root: str
    worktrees: list[WorktreeSpec] = field(default_factory=list)
    report_dirs: list[str] = field(default_factory=list)


@dataclass
class ExternalVolume:
    mount: str
    models_dir: str
    data_dir: str
    volume_uuid: str | None = None
    volume_name: str | None = None


@dataclass
class AppConfig:
    schema_version: int
    timezone: str
    policy_version: str
    home: str
    external: ExternalVolume
    projects: list[ProjectSpec]
    ollama_host: str = "127.0.0.1:11434"
    ollama_no_cloud: bool = True
    context_length: int = 8192
    max_loaded_models: int = 1
    lite_model: str = "gemma4:12b"
    depth_candidates: list[str] = field(
        default_factory=lambda: ["qwen3-coder:30b", "qwen3.6:35b"]
    )

    def project(self, project_id: str) -> ProjectSpec:
        for p in self.projects:
            if p.project_id == project_id:
                return p
        raise KeyError(f"unknown project: {project_id}")

    def scannable_worktrees(self, project_id: str | None = None) -> list[tuple[ProjectSpec, WorktreeSpec]]:
        out: list[tuple[ProjectSpec, WorktreeSpec]] = []
        for p in self.projects:
            if project_id and p.project_id != project_id:
                continue
            for wt in p.worktrees:
                if wt.scan:
                    out.append((p, wt))
        return out


def default_projects() -> list[ProjectSpec]:
    root = "/Users/xiuqiu/WorkSpace/theAIapp-service"
    return [
        ProjectSpec(
            project_id="website",
            display_name="The AI 官网后端",
            approved_root=root,
            worktrees=[
                WorktreeSpec(
                    worktree_id="website-main",
                    path=root,
                    scan=True,
                    notes="主工作树 feat/website-backend-v0.1",
                ),
                WorktreeSpec(
                    worktree_id="website-auth",
                    path="/Users/xiuqiu/WorkSpace/theAIapp-service-auth-publishing",
                    scan=False,
                    notes="登记但不扫描",
                ),
                WorktreeSpec(
                    worktree_id="website-integration",
                    path="/Users/xiuqiu/WorkSpace/theAIapp-service-integration",
                    scan=False,
                    notes="登记但不扫描；有未提交改动",
                ),
            ],
        )
    ]


def _as_dict(cfg: AppConfig) -> dict[str, Any]:
    return {
        "schema_version": cfg.schema_version,
        "timezone": cfg.timezone,
        "policy_version": cfg.policy_version,
        "home": cfg.home,
        "ollama_host": cfg.ollama_host,
        "ollama_no_cloud": cfg.ollama_no_cloud,
        "context_length": cfg.context_length,
        "max_loaded_models": cfg.max_loaded_models,
        "lite_model": cfg.lite_model,
        "depth_candidates": cfg.depth_candidates,
        "external": {
            "mount": cfg.external.mount,
            "models_dir": cfg.external.models_dir,
            "data_dir": cfg.external.data_dir,
            "volume_uuid": cfg.external.volume_uuid,
            "volume_name": cfg.external.volume_name,
        },
        "projects": [
            {
                "project_id": p.project_id,
                "display_name": p.display_name,
                "approved_root": p.approved_root,
                "report_dirs": p.report_dirs,
                "worktrees": [
                    {
                        "worktree_id": w.worktree_id,
                        "path": w.path,
                        "scan": w.scan,
                        "notes": w.notes,
                    }
                    for w in p.worktrees
                ],
            }
            for p in cfg.projects
        ],
    }


def _from_dict(data: dict[str, Any], *, home: Path) -> AppConfig:
    ext = data.get("external") or {}
    projects = []
    for p in data.get("projects") or []:
        projects.append(
            ProjectSpec(
                project_id=p["project_id"],
                display_name=p.get("display_name") or p["project_id"],
                approved_root=p["approved_root"],
                report_dirs=list(p.get("report_dirs") or []),
                worktrees=[
                    WorktreeSpec(
                        worktree_id=w["worktree_id"],
                        path=w["path"],
                        scan=bool(w.get("scan", True)),
                        notes=w.get("notes") or "",
                    )
                    for w in p.get("worktrees") or []
                ],
            )
        )
    return AppConfig(
        schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
        timezone=data.get("timezone") or "Asia/Taipei",
        policy_version=data.get("policy_version") or POLICY_VERSION,
        home=str(home),
        external=ExternalVolume(
            mount=ext.get("mount") or str(DEFAULT_EXTERNAL_MOUNT),
            models_dir=ext.get("models_dir") or str(DEFAULT_MODELS_DIR),
            data_dir=ext.get("data_dir") or str(DEFAULT_EXTERNAL_DATA),
            volume_uuid=ext.get("volume_uuid"),
            volume_name=ext.get("volume_name"),
        ),
        projects=projects or default_projects(),
        ollama_host=data.get("ollama_host") or "127.0.0.1:11434",
        ollama_no_cloud=bool(data.get("ollama_no_cloud", True)),
        context_length=int(data.get("context_length") or 8192),
        max_loaded_models=int(data.get("max_loaded_models") or 1),
        lite_model=data.get("lite_model") or "gemma4:12b",
        depth_candidates=list(data.get("depth_candidates") or ["qwen3-coder:30b", "qwen3.6:35b"]),
    )


def host_timezone() -> str:
    tz = os.environ.get("TZ")
    if tz:
        return tz
    try:
        link = os.readlink("/etc/localtime")
        marker = "/zoneinfo/"
        if marker in link:
            return link.split(marker, 1)[1]
    except OSError:
        pass
    return "Asia/Taipei"


def default_config(home: Path | None = None) -> AppConfig:
    home = (home or default_home()).resolve()
    return AppConfig(
        schema_version=SCHEMA_VERSION,
        timezone=host_timezone(),
        policy_version=POLICY_VERSION,
        home=str(home),
        external=ExternalVolume(
            mount=str(DEFAULT_EXTERNAL_MOUNT),
            models_dir=str(DEFAULT_MODELS_DIR),
            data_dir=str(DEFAULT_EXTERNAL_DATA),
            volume_uuid=DEFAULT_EXTERNAL_UUID,
            volume_name="LocalDevData",
        ),
        projects=default_projects(),
    )


def load_config(home: Path | None = None) -> AppConfig:
    home = (home or default_home()).resolve()
    path = layout(home)["config"]
    if not path.exists():
        return default_config(home)
    data = json.loads(path.read_text(encoding="utf-8"))
    return _from_dict(data, home=home)


def save_config(cfg: AppConfig) -> Path:
    home = Path(cfg.home)
    path = layout(home)["config"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        if not path.parent.is_dir():
            raise
    payload = json.dumps(_as_dict(cfg), indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except PermissionError:
        path.write_text(payload, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except PermissionError:
        pass
    return path


def volume_present(cfg: AppConfig) -> bool:
    """Identity-checked. A path name alone is never enough."""
    from xiaomao.volume import is_usable

    return is_usable(cfg.external.mount, cfg.external.volume_uuid)


def volume_report(cfg: AppConfig) -> dict[str, object]:
    from xiaomao.volume import verify_mount

    return verify_mount(cfg.external.mount, cfg.external.volume_uuid)


class VolumeIdentityError(RuntimeError):
    """Raised when the mount at the configured path is not the expected volume."""


def require_external_volume(cfg: AppConfig) -> dict[str, object]:
    """Fail closed. Never fall back to trusting the path name.

    Callers that need the external volume (model load, archive write) must go
    through this. A `mismatch` means some other disk is mounted at the same
    path, so refusing is the only safe answer.
    """
    report = volume_report(cfg)
    status = report["status"]
    if not report["mounted"]:
        raise VolumeIdentityError(
            f"外盘未挂载：{report['mount']}（{report['note']}）。模型/归档任务应 deferred。"
        )
    if status == "mismatch":
        raise VolumeIdentityError(
            f"外盘身份不符：{report['mount']} 期望 {report['expected_uuid']}，"
            f"实际 {report['actual_uuid']}。拒绝使用。"
        )
    if status == "unknown":
        raise VolumeIdentityError(
            f"无法确认外盘身份：{report['mount']}（{report['note']}）。拒绝使用。"
        )
    return report


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def ollama_reachable(host: str) -> bool:
    if ":" in host:
        name, port_s = host.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            return False
    else:
        name, port = host, 11434
    try:
        with socket.create_connection((name, port), timeout=0.4):
            return True
    except OSError:
        return False


def host_identity() -> dict[str, Any]:
    """Safe hardware summary — no serials, no UUIDs of the machine."""
    info: dict[str, Any] = {
        "hostname_kind": platform.node().split(".")[-1] if platform.node() else "unknown",
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }
    try:
        import subprocess

        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip().isdigit():
            info["memory_bytes"] = int(out.stdout.strip())
    except OSError:
        pass
    return info
