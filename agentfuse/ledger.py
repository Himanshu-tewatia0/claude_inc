"""Spend ledger.

The core idea: RESERVE the worst-case cost of a call BEFORE it is made, inside one SQLite write
transaction (BEGIN IMMEDIATE), so concurrent threads and processes cannot jointly overspend.
After the call, SETTLE with the real cost and free the unused headroom.
Money is integer micro-dollars, so there is no floating-point drift.
"""
from __future__ import annotations

import math
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .errors import BudgetExceeded

UNIT = 1_000_000  # micro-dollars per dollar
WINDOWS = {"run": None, "all": None, "minute": 60, "hour": 3600, "day": 86400, "month": 30 * 86400}
SCOPE_COL = {"run": "run_id", "user": "user_id", "org": "org_id", "global": None}


def to_micro(usd: float) -> int:
    """Dollars to integer micro-dollars, rounding UP (never under-count)."""
    return int(math.ceil(usd * UNIT - 1e-9))


@dataclass(frozen=True)
class Limit:
    scope: str  # "run" | "user" | "org" | "global"
    per: str    # "run" | "all" | "minute" | "hour" | "day" | "month"  (rolling windows)
    usd: float

    def __post_init__(self):
        if self.scope not in SCOPE_COL:
            raise ValueError(f"scope must be one of {sorted(SCOPE_COL)}")
        if self.per not in WINDOWS:
            raise ValueError(f"per must be one of {sorted(WINDOWS)}")

    @property
    def micro(self) -> int:
        return to_micro(self.usd)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls(
  call_id  TEXT PRIMARY KEY,
  ts       REAL NOT NULL,
  run_id   TEXT, user_id TEXT, org_id TEXT, model TEXT,
  state    TEXT NOT NULL,          -- open | settled | released | expired
  reserved INTEGER NOT NULL,
  settled  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_calls_ts   ON calls(ts);
CREATE INDEX IF NOT EXISTS ix_calls_run  ON calls(run_id);
CREATE INDEX IF NOT EXISTS ix_calls_user ON calls(user_id);
CREATE INDEX IF NOT EXISTS ix_calls_org  ON calls(org_id);
CREATE TABLE IF NOT EXISTS breakers(
  scope_key TEXT PRIMARY KEY, opened_at REAL NOT NULL, reason TEXT
);
"""


class Ledger:
    def __init__(self, path: str = ":memory:", clock: Callable[[], float] = time.time,
                 reservation_ttl_s: float = 300.0):
        self.clock = clock
        self.ttl = reservation_ttl_s
        self._lock = threading.RLock()
        self._c = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        try:
            self._c.execute("PRAGMA journal_mode=WAL")
            self._c.executescript(_SCHEMA)
        except BaseException:
            self._c.close()  # never leave a half-built ledger holding the file open (matters on Windows)
            raise

    def close(self) -> None:
        with self._lock:
            self._c.close()

    # ---- internal ----
    def _spent(self, lim: Limit, keys: dict, now: float) -> int:
        where = ["state IN ('open','settled')"]
        params: list = []
        col = SCOPE_COL[lim.scope]
        if col:
            where.append(f"{col} = ?")
            params.append(keys[lim.scope])
        win = WINDOWS[lim.per]
        if win:
            where.append("ts >= ?")
            params.append(now - win)
        sql = ("SELECT COALESCE(SUM(CASE state WHEN 'open' THEN reserved ELSE settled END), 0) "
               "FROM calls WHERE " + " AND ".join(where))
        return int(self._c.execute(sql, params).fetchone()[0])

    # ---- public ----
    def reserve(self, *, run_id: str, user_id: str, org_id: str, est_micro: int,
                limits: Iterable[Limit], model: str = "") -> str:
        """Atomically check every limit and reserve est_micro. Raises BudgetExceeded BEFORE any spend."""
        now = self.clock()
        keys = {"run": run_id, "user": user_id, "org": org_id}
        with self._lock:
            c = self._c
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute("UPDATE calls SET state='expired' WHERE state='open' AND ts < ?",
                          (now - self.ttl,))
                for lim in limits:
                    spent = self._spent(lim, keys, now)
                    if spent + est_micro > lim.micro:
                        raise BudgetExceeded(lim, spent, est_micro)
                call_id = uuid.uuid4().hex
                c.execute(
                    "INSERT INTO calls(call_id, ts, run_id, user_id, org_id, model, state, reserved) "
                    "VALUES (?,?,?,?,?,?,'open',?)",
                    (call_id, now, run_id, user_id, org_id, model, est_micro),
                )
                c.execute("COMMIT")
                return call_id
            except BaseException:
                c.execute("ROLLBACK")
                raise

    def settle(self, call_id: str, actual_micro: int) -> int:
        """Record the real cost. Returns overspend vs the reservation (positive = we under-estimated)."""
        with self._lock:
            row = self._c.execute("SELECT reserved FROM calls WHERE call_id=?", (call_id,)).fetchone()
            reserved = int(row[0]) if row else 0
            self._c.execute("UPDATE calls SET state='settled', settled=? WHERE call_id=?",
                            (actual_micro, call_id))
            return actual_micro - reserved

    def release(self, call_id: str) -> None:
        """The call failed before spending anything: free the reservation."""
        with self._lock:
            self._c.execute("UPDATE calls SET state='released' WHERE call_id=? AND state='open'", (call_id,))

    def spent_micro(self, scope: str, key: Optional[str], per: str) -> int:
        lim = Limit(scope, per, 0)
        with self._lock:
            return self._spent(lim, {scope: key}, self.clock())

    def summary(self) -> dict:
        """Everything the CLI shows, computed from the database (nothing hard-coded)."""
        now = self.clock()
        spent_expr = "SUM(CASE state WHEN 'open' THEN reserved ELSE settled END)"
        with self._lock:
            c = self._c
            out: dict = {}
            for name, win in (("minute", 60), ("hour", 3600), ("day", 86400), ("all", None)):
                q = f"SELECT COALESCE({spent_expr},0), COUNT(*) FROM calls WHERE state IN ('open','settled')"
                params: tuple = ()
                if win:
                    q += " AND ts >= ?"
                    params = (now - win,)
                usd, n = c.execute(q, params).fetchone()
                out[name] = {"usd": usd / UNIT, "calls": n}
            out["open_reservations"] = c.execute("SELECT COUNT(*) FROM calls WHERE state='open'").fetchone()[0]
            out["top_users_day"] = [
                (u, s / UNIT) for u, s in c.execute(
                    f"SELECT user_id, {spent_expr} AS s FROM calls WHERE state IN ('open','settled') "
                    "AND ts >= ? GROUP BY user_id ORDER BY s DESC LIMIT 5", (now - 86400,))]
            out["breakers"] = [(k, why, now - t) for k, t, why in c.execute(
                "SELECT scope_key, opened_at, reason FROM breakers")]
            return out

    # ---- circuit breakers ----
    def trip(self, scope_key: str, reason: str) -> None:
        with self._lock:
            self._c.execute("INSERT OR REPLACE INTO breakers(scope_key, opened_at, reason) VALUES (?,?,?)",
                            (scope_key, self.clock(), reason))

    def breaker(self, scope_key: str, cooldown_s: float) -> Optional[str]:
        """Return the reason if the breaker is open, else None (auto-closes after the cooldown)."""
        with self._lock:
            row = self._c.execute("SELECT opened_at, reason FROM breakers WHERE scope_key=?",
                                  (scope_key,)).fetchone()
            if not row:
                return None
            if self.clock() - row[0] >= cooldown_s:
                self._c.execute("DELETE FROM breakers WHERE scope_key=?", (scope_key,))
                return None
            return row[1]

    def reset(self, scope_key: str) -> None:
        with self._lock:
            self._c.execute("DELETE FROM breakers WHERE scope_key=?", (scope_key,))
