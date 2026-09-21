from __future__ import annotations

import os
from pathlib import Path


class ScanLock:
    """Simple exclusive lock so two scans cannot share one data dir."""

    def __init__(self, path: Path, *, retries: int = 0, retry_s: float = 1.0):
        self.path = path
        self.retries = retries
        self.retry_s = retry_s
        self._fd: int | None = None

    def __enter__(self) -> "ScanLock":
        import time

        self.path.parent.mkdir(parents=True, exist_ok=True)
        attempts = self.retries + 1
        last_exc: OSError | None = None
        for i in range(attempts):
            fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                os.write(fd, str(os.getpid()).encode())
                return self
            except BlockingIOError as exc:
                os.close(fd)
                last_exc = exc
                if i + 1 < attempts:
                    time.sleep(self.retry_s)
                    continue
                raise RuntimeError(f"another xiaomao scan holds {self.path}") from exc
            except OSError:
                os.close(fd)
                raise
        raise RuntimeError(f"another xiaomao scan holds {self.path}") from last_exc

    def __exit__(self, *exc: object) -> None:
        if self._fd is None:
            return
        try:
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
