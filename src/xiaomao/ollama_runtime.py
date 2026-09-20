"""Ollama process checks. Settings files are not evidence of effect.

Model files live on the identity-checked external volume. Missing or mismatched
volume → refuse. Never fall back to ~/.ollama/models.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from xiaomao.config import AppConfig, VolumeIdentityError, require_external_volume, which
from xiaomao.paths import DEFAULT_EXTERNAL_MOUNT, LEGACY_MODELS_DIR

DEFAULT_KEEP_ALIVE = "0"
FORBIDDEN_FALLBACK = Path.home() / ".ollama" / "models"


class ModelPathError(RuntimeError):
    pass


def models_dir_allowed(cfg: AppConfig) -> Path:
    report = require_external_volume(cfg)
    models = Path(cfg.external.models_dir).expanduser().resolve()
    mount = Path(cfg.external.mount).resolve()
    try:
        models.relative_to(mount)
    except ValueError as exc:
        raise ModelPathError(f"模型目录不在已校验外盘上：{models} mount={mount}") from exc
    fallback = FORBIDDEN_FALLBACK.resolve()
    try:
        models.relative_to(fallback)
        raise ModelPathError("拒绝使用 ~/.ollama/models 作为回退目录")
    except ValueError:
        pass
    if not report["mounted"] or report["status"] not in {"match", "unverified"}:
        raise VolumeIdentityError(f"外盘不可用：{report}")
    if report["status"] == "unverified":
        # usable with warning; callers should surface it.
        pass
    return models


def refuse_internal_download(cfg: AppConfig) -> None:
    models_dir_allowed(cfg)


def ollama_bin() -> str | None:
    found = which("ollama")
    if found:
        return found
    bundled = Path("/Applications/Ollama.app/Contents/Resources/ollama")
    if bundled.exists():
        return str(bundled)
    local = Path("/usr/local/bin/ollama")
    if local.exists():
        return str(local)
    return None


def parse_host(host: str) -> tuple[str, int]:
    if ":" in host:
        name, port_s = host.rsplit(":", 1)
        return name, int(port_s)
    return host, 11434


def _url(cfg: AppConfig, path: str) -> str:
    name, port = parse_host(cfg.ollama_host)
    return f"http://{name}:{port}{path}"


def api_get(cfg: AppConfig, path: str, timeout: float = 2.0) -> dict[str, Any] | None:
    req = urllib.request.Request(_url(cfg, path), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def api_post(cfg: AppConfig, path: str, payload: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _url(cfg, path),
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body.decode("utf-8"))


def running_ollama_pids() -> list[int]:
    proc = subprocess.run(
        ["pgrep", "-x", "ollama"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if proc.returncode not in (0, 1):
        return []
    pids = []
    for line in proc.stdout.split():
        if line.isdigit():
            pids.append(int(line))
    return pids


def process_environ(pid: int) -> dict[str, str]:
    """Best-effort process env via `ps eww`. Not a settings file."""
    proc = subprocess.run(
        ["ps", "eww", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return {}
    env: dict[str, str] = {}
    # `ps eww` appends env as KEY=VAL tokens after the command on macOS.
    text = proc.stdout.strip("\n")
    parts = text.split(" ")
    for token in parts:
        if "=" in token and token[0].isalpha():
            k, _, v = token.partition("=")
            if k.isidentifier() or k.startswith("OLLAMA_"):
                env[k] = v
    return env


def inspect_running(cfg: AppConfig) -> dict[str, Any]:
    pids = running_ollama_pids()
    envs = [process_environ(pid) for pid in pids]
    tags = api_get(cfg, "/api/tags")
    name, port = parse_host(cfg.ollama_host)
    listening_loopback = name in {"127.0.0.1", "localhost", "::1"}
    return {
        "pids": pids,
        "process_env": envs,
        "host": cfg.ollama_host,
        "listening_loopback": listening_loopback,
        "reachable": tags is not None,
        "tags": tags,
        "configured": {
            "OLLAMA_HOST": cfg.ollama_host,
            "OLLAMA_NO_CLOUD": "1" if cfg.ollama_no_cloud else "0",
            "OLLAMA_CONTEXT_LENGTH": str(cfg.context_length),
            "OLLAMA_MAX_LOADED_MODELS": str(cfg.max_loaded_models),
            "OLLAMA_KEEP_ALIVE": DEFAULT_KEEP_ALIVE,
            "OLLAMA_MODELS": cfg.external.models_dir,
        },
    }


def dir_in_use(path: Path) -> list[str]:
    """Return lsof lines if any process has the directory open."""
    if not path.exists():
        return []
    proc = subprocess.run(
        ["lsof", "+D", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return lines[1:] if lines else []


class OllamaClient:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def generate_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        models_dir_allowed(self.cfg)
        payload = {
            "model": model,
            "stream": False,
            "keep_alive": 0,
            "format": schema,
            "options": {
                "num_ctx": int(self.cfg.context_length),
                "num_predict": 1024,
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        raw = api_post(self.cfg, "/api/chat", payload, timeout=timeout)
        content = ""
        msg = raw.get("message") or {}
        if isinstance(msg, dict):
            content = msg.get("content") or ""
        parsed: dict[str, Any] | None
        try:
            parsed = json.loads(content) if content else None
        except json.JSONDecodeError:
            parsed = None
        return {
            "model": raw.get("model") or model,
            "digest": None,
            "json": parsed,
            "content": content,
            "raw_keys": sorted(raw.keys()),
            "eval_duration": raw.get("eval_duration"),
            "load_duration": raw.get("load_duration"),
            "prompt_eval_count": raw.get("prompt_eval_count"),
            "eval_count": raw.get("eval_count"),
        }

    def stop(self, model: str) -> None:
        bin_ = ollama_bin()
        if not bin_:
            return
        subprocess.run([bin_, "stop", model], capture_output=True, text=True, timeout=30, check=False)


def serve_env(cfg: AppConfig) -> dict[str, str]:
    models = str(models_dir_allowed(cfg))
    env = os.environ.copy()
    env["OLLAMA_HOST"] = cfg.ollama_host
    env["OLLAMA_MODELS"] = models
    env["OLLAMA_NO_CLOUD"] = "1" if cfg.ollama_no_cloud else "0"
    env["OLLAMA_CONTEXT_LENGTH"] = str(cfg.context_length)
    env["OLLAMA_MAX_LOADED_MODELS"] = str(cfg.max_loaded_models)
    env["OLLAMA_NUM_PARALLEL"] = "1"
    env["OLLAMA_KEEP_ALIVE"] = DEFAULT_KEEP_ALIVE
    return env


def bind_is_loopback(cfg: AppConfig) -> bool:
    name, _ = parse_host(cfg.ollama_host)
    if name not in {"127.0.0.1", "localhost", "::1"}:
        return False
    try:
        info = socket.getaddrinfo(name, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for item in info:
        ip = item[4][0]
        if ip not in {"127.0.0.1", "::1"}:
            return False
    return True


def legacy_models_present() -> bool:
    return Path(LEGACY_MODELS_DIR).exists()


def default_mount() -> Path:
    return Path(DEFAULT_EXTERNAL_MOUNT)
