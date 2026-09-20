#!/usr/bin/env python3.11
"""Two-process flock check. Used by tests and accept, not by the scan job."""
from __future__ import annotations

import os
import sys
import time
from multiprocessing import Process, Queue
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from xiaomao.lock import ScanLock  # noqa: E402


def worker(path: str, q: Queue, delay: float) -> None:
    time.sleep(delay)
    try:
        with ScanLock(Path(path)):
            q.put("held")
            time.sleep(0.5)
            q.put("released")
    except RuntimeError:
        q.put("blocked")


def main() -> int:
    lock = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("xiaomao.lock")
    q: Queue = Queue()
    p1 = Process(target=worker, args=(str(lock), q, 0.0))
    p2 = Process(target=worker, args=(str(lock), q, 0.05))
    p1.start()
    p2.start()
    p1.join(5)
    p2.join(5)
    events = []
    while not q.empty():
        events.append(q.get())
    print("lock_events", events)
    ok = events.count("held") == 1 and events.count("blocked") == 1 and events.count("released") == 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
