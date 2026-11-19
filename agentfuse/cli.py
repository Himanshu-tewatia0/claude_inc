"""agentfuse command line. Every number comes from the ledger database or the audit file.
It never prints reassurance it cannot prove."""
from __future__ import annotations

import argparse
import os
import sys

from .audit import verify_chain
from .ledger import Ledger


def _status(db: str) -> int:
    if not os.path.exists(db):
        print(f"No ledger at {os.path.abspath(db)}. Nothing has been recorded there yet.")
        return 1
    led = Ledger(db)
    try:
        s = led.summary()
    finally:
        led.close()
    print(f"Ledger: {os.path.abspath(db)}")
    for k in ("minute", "hour", "day", "all"):
        print(f"  last {k:<6} ${s[k]['usd']:.4f}  ({s[k]['calls']} calls)")
    print(f"  open reservations: {s['open_reservations']}")
    if s["top_users_day"]:
        print("  top users (24h): " + ", ".join(f"{u} ${v:.4f}" for u, v in s["top_users_day"]))
    if s["breakers"]:
        print("  OPEN BREAKERS:")
        for key, why, age in s["breakers"]:
            print(f"    {key}  opened {age:.0f}s ago: {why}")
    else:
        print("  open breakers: none")
    print("Figures count reserved worst-case cost for in-flight calls and real usage for finished ones.")
    return 0


def _verify(path: str) -> int:
    if not os.path.exists(path):
        print(f"No audit file at {path}")
        return 1
    ok, bad = verify_chain(path)
    print("audit chain intact" if ok else f"audit chain BROKEN at line {bad}")
    return 0 if ok else 2


def _reset(db: str, key: str) -> int:
    if not os.path.exists(db):
        print(f"No ledger at {db}")
        return 1
    led = Ledger(db)
    try:
        led.reset(key)
    finally:
        led.close()
    print(f"breaker '{key}' cleared")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agentfuse")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="show spend and open breakers from a ledger file")
    st.add_argument("--db", required=True)
    vf = sub.add_parser("verify", help="check an audit log's hash chain")
    vf.add_argument("audit")
    rs = sub.add_parser("reset", help="clear a circuit breaker, e.g. run:abc123, user:u1, or global")
    rs.add_argument("--db", required=True)
    rs.add_argument("key")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        return _status(a.db)
    if a.cmd == "verify":
        return _verify(a.audit)
    return _reset(a.db, a.key)


if __name__ == "__main__":
    sys.exit(main())
