"""Compress git path lists into module names. Never opens a business file."""

from __future__ import annotations

from pathlib import PurePosixPath

# Wallet-Infrastructure bulk that is never a "today's module" for the menu.
SKIP_PREFIXES = (
    "web/console",
    "tmp/",
    "node_modules/",
    ".git/",
    "evidence/",
)
SKIP_NAMES = {".git", "node_modules", "tmp", "evidence"}


def path_is_skipped(relpath: str) -> bool:
    rel = relpath.replace("\\", "/").lstrip("./")
    if rel.startswith(SKIP_PREFIXES) or rel in {"web/console", "tmp", "node_modules", ".git", "evidence"}:
        return True
    parts = PurePosixPath(rel).parts
    return bool(parts) and parts[0] in SKIP_NAMES


def module_of(relpath: str) -> str | None:
    """First one or two directory levels. Filenames are never modules.

    `internal/funds/ledger.go` → `internal/funds`; `src/foo.py` → `src`;
    a root file such as `README.md` is dropped.
    """
    rel = relpath.replace("\\", "/").lstrip("./")
    if not rel or path_is_skipped(rel):
        return None
    path = PurePosixPath(rel)
    parts = [p for p in path.parts if p not in {".", ".."}]
    if not parts:
        return None
    # name-status entries are files: drop the basename, keep 1–2 parent dirs.
    dirs = parts[:-1]
    if not dirs:
        return None
    if len(dirs) == 1:
        return dirs[0]
    return f"{dirs[0]}/{dirs[1]}"


def modules_from_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        name = module_of(path)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def classify_module_changes(
    added: list[str],
    deleted: list[str],
    modified: list[str],
) -> dict[str, list[str]]:
    """A module is new/removed only when the whole directory appears or vanishes.

    Editing a file inside an existing module is a modification, not a removal.
    """
    added_mods = set(modules_from_paths(added))
    deleted_mods = set(modules_from_paths(deleted))
    modified_mods = set(modules_from_paths(modified))
    new_modules = sorted(added_mods - deleted_mods - modified_mods)
    gone_modules = sorted(deleted_mods - added_mods - modified_mods)
    changed_modules = sorted((added_mods | deleted_mods | modified_mods) - set(new_modules) - set(gone_modules))
    return {
        "new_modules": new_modules,
        "gone_modules": gone_modules,
        "changed_modules": changed_modules,
    }
