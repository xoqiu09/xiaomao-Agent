from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "Xiaomao"
DEFAULT_EXTERNAL_MOUNT = Path("/Volumes/LocalDevData")
DEFAULT_EXTERNAL_DATA = DEFAULT_EXTERNAL_MOUNT / "Xiaomao"
# Target layout: bulk, rebuildable assets live on the external volume.
DEFAULT_MODELS_DIR = DEFAULT_EXTERNAL_DATA / "ollama"
LEGACY_MODELS_DIR = DEFAULT_EXTERNAL_MOUNT / "Models" / "ollama"
DEFAULT_EXTERNAL_UUID = "44c5480a-388c-475e-a320-a49b226a5953"
STORAGE_BUDGET_BYTES = 2 * 1024 * 1024 * 1024
POLICY_VERSION = "1"


def default_home() -> Path:
    override = os.environ.get("XIAOMAO_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / "Library" / "Application Support" / APP_DIR_NAME).resolve()


def layout(home: Path) -> dict[str, Path]:
    home = home.expanduser()
    return {
        "home": home,
        "config": home / "config.json",
        "db": home / "xiaomao.sqlite",
        "evidence": home / "evidence",
        "reports": home / "reports",
        "reports_daily": home / "reports" / "daily",
        "reports_projects": home / "reports" / "projects",
        "reports_handoff": home / "reports" / "handoff",
        "reports_briefing": home / "reports" / "briefing",
        "logs": home / "logs",
        "cache": home / "cache",
        "lock": home / "xiaomao.lock",
    }


def ensure_layout(home: Path) -> dict[str, Path]:
    paths = layout(home)
    for key in (
        "home",
        "evidence",
        "reports",
        "reports_daily",
        "reports_projects",
        "reports_handoff",
        "reports_briefing",
        "logs",
        "cache",
    ):
        try:
            paths[key].mkdir(parents=True, exist_ok=True)
        except PermissionError:
            # Sandbox or OS may block mkdir even when the directory already
            # exists from a previous privileged create. Continue if usable.
            if not paths[key].is_dir():
                raise
    return paths
