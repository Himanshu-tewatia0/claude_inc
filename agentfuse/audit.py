"""Append-only audit log. Each line carries the hash of the previous line, so editing or deleting a line
is detectable. By design it stores decisions, costs and counts, never raw prompts or outputs."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Optional, Tuple

GENESIS = "0" * 64


def _digest(prev: str, record: dict) -> str:
    body = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._prev = GENESIS
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self._prev = json.loads(line)["hash"]

    def write(self, record: dict) -> None:
        with self._lock:
            rec = dict(record)
            rec["ts"] = round(time.time(), 3)
            rec["prev"] = self._prev
            h = _digest(self._prev, {k: v for k, v in rec.items() if k != "prev"})
            rec["hash"] = h
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            self._prev = h


def verify_chain(path: str) -> Tuple[bool, Optional[int]]:
    """Return (ok, first_bad_line_number)."""
    prev = GENESIS
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            rec = json.loads(line)
            claimed = rec.pop("hash", None)
            if rec.get("prev") != prev:
                return False, i
            if _digest(prev, {k: v for k, v in rec.items() if k != "prev"}) != claimed:
                return False, i
            prev = claimed
    return True, None
