"""Runaway detection: the same request repeating, too many steps, or too much wall time in one run."""
from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import deque
from typing import Callable, Iterable

from .errors import LoopDetected

_DIGITS = re.compile(r"\d+")
_WS = re.compile(r"\s+")


def signature(model: str, text_tail: str, tool_names: Iterable[str] = ()) -> str:
    """Stable fingerprint of a request. Digits are masked so a loop that only changes a counter still matches."""
    norm = _WS.sub(" ", _DIGITS.sub("#", text_tail.lower())).strip()[-2000:]
    raw = model + "|" + ",".join(sorted(tool_names)) + "|" + norm
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


class LoopDetector:
    def __init__(self, window: int = 20, repeat_limit: int = 3, max_steps: int = 100,
                 max_wall_s: float = 900.0, clock: Callable[[], float] = time.time):
        self.window, self.repeat_limit = window, repeat_limit
        self.max_steps, self.max_wall_s, self.clock = max_steps, max_wall_s, clock
        self._runs: dict = {}
        self._lock = threading.Lock()

    def check(self, run_id: str, sig: str) -> None:
        now = self.clock()
        with self._lock:
            if len(self._runs) > 5000:  # crude garbage collection of old runs
                for rid in sorted(self._runs, key=lambda r: self._runs[r]["start"])[:1000]:
                    del self._runs[rid]
            st = self._runs.setdefault(run_id, {"start": now, "steps": 0, "sigs": deque(maxlen=self.window)})
            st["steps"] += 1
            st["sigs"].append(sig)
            if st["steps"] > self.max_steps:
                raise LoopDetected(f"run {run_id}: more than {self.max_steps} model calls")
            if now - st["start"] > self.max_wall_s:
                raise LoopDetected(f"run {run_id}: longer than {self.max_wall_s:.0f}s")
            if st["sigs"].count(sig) >= self.repeat_limit:
                raise LoopDetected(
                    f"run {run_id}: the same request appeared {self.repeat_limit} times in the last {self.window} calls")
