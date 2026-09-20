"""Volume identity via getattrlist(2).

`diskutil` needs the DiskManagement framework, which is unavailable inside a
sandboxed session. `getattrlist` is a plain syscall and returns the same APFS
volume UUID, so mount checks never have to trust a path name alone.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import struct
import uuid
from pathlib import Path

ATTR_BIT_MAP_COUNT = 5
ATTR_VOL_INFO = 0x80000000
ATTR_VOL_UUID = 0x00040000
ATTR_VOL_NAME = 0x00002000

_MIN_UUID_ATTRLEN = 20


class _AttrList(ctypes.Structure):
    _fields_ = [
        ("bitmapcount", ctypes.c_ushort),
        ("reserved", ctypes.c_ushort),
        ("commonattr", ctypes.c_uint),
        ("volattr", ctypes.c_uint),
        ("dirattr", ctypes.c_uint),
        ("fileattr", ctypes.c_uint),
        ("forkattr", ctypes.c_uint),
    ]


def _libc() -> ctypes.CDLL | None:
    name = ctypes.util.find_library("c")
    if not name:
        return None
    try:
        return ctypes.CDLL(name, use_errno=True)
    except OSError:
        return None


def volume_uuid(path: str | os.PathLike[str]) -> tuple[str | None, str]:
    """Return (uuid_or_none, diagnostic). Never raises."""
    target = Path(path)
    if not target.exists():
        return None, "path does not exist"
    libc = _libc()
    if libc is None:
        return None, "libc unavailable"

    attrs = _AttrList()
    attrs.bitmapcount = ATTR_BIT_MAP_COUNT
    attrs.volattr = ATTR_VOL_INFO | ATTR_VOL_UUID
    buf = ctypes.create_string_buffer(256)
    rc = libc.getattrlist(
        str(target).encode("utf-8"),
        ctypes.byref(attrs),
        buf,
        ctypes.sizeof(buf),
        0,
    )
    if rc != 0:
        err = ctypes.get_errno()
        return None, f"getattrlist errno={err} {os.strerror(err)}"

    attrlen = struct.unpack_from("I", buf.raw, 0)[0]
    if attrlen < _MIN_UUID_ATTRLEN:
        return None, f"volume reported no uuid (attrlen={attrlen})"
    try:
        return str(uuid.UUID(bytes=buf.raw[4:20])), "getattrlist"
    except ValueError as exc:
        return None, f"malformed uuid: {exc}"


def verify_mount(mount: str | os.PathLike[str], expected_uuid: str | None) -> dict[str, object]:
    """Check a mount by identity, not by path name."""
    target = Path(mount)
    present = target.is_dir() and os.path.ismount(target)
    actual, note = volume_uuid(target) if target.exists() else (None, "not mounted")

    if expected_uuid is None:
        status = "unverified"
    elif actual is None:
        status = "unknown"
    elif actual.lower() == expected_uuid.lower():
        status = "match"
    else:
        status = "mismatch"

    return {
        "mount": str(target),
        "mounted": present,
        "expected_uuid": expected_uuid,
        "actual_uuid": actual,
        "status": status,
        "note": note,
    }


def is_usable(mount: str | os.PathLike[str], expected_uuid: str | None) -> bool:
    """True only when the volume is mounted and identity does not contradict config."""
    result = verify_mount(mount, expected_uuid)
    if not result["mounted"]:
        return False
    return result["status"] in {"match", "unverified"}
