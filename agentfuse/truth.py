"""Deterministic numeric firewall.

Rule: every number in a model output must be traceable to the evidence (prompt, documents, tool results).
It checks TRACEABILITY, not truth: it cannot tell whether the evidence itself is right, and it cannot
catch a correct number attached to the wrong subject. No LLM is used, so results are repeatable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional

from .errors import UngroundedNumber

_MULT = {
    "k": 10**3, "thousand": 10**3,
    "m": 10**6, "mn": 10**6, "million": 10**6,
    "b": 10**9, "bn": 10**9, "billion": 10**9,
    "lakh": 10**5, "lakhs": 10**5, "lac": 10**5, "lacs": 10**5,
    "crore": 10**7, "crores": 10**7, "cr": 10**7,
}
_CUR = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "$": "USD", "usd": "USD",
        "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP"}

_NUM = re.compile(
    r"(?<![\w.])"
    r"(?P<cur>₹|\$|€|£|Rs\.?\s?|INR\s?|USD\s?|EUR\s?|GBP\s?)?"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suf>%|\s?(?:percent|crores?|cr|lakhs?|lacs?|bn|billion|mn|million|thousand)\b|[kKmMbB]\b)?",
    re.IGNORECASE,
)
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_NOISE = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s" + _MONTH + r",?\s\d{4}\b", re.IGNORECASE),
    re.compile(r"\b" + _MONTH + r"\s\d{1,2}(?:st|nd|rd|th)?,?\s\d{4}\b", re.IGNORECASE),
    re.compile(r"(?m)^[ \t]*(?:\d{1,3}[.)]|[-*\u2022])[ \t]"),  # list markers like "1." or "-"
]


@dataclass
class Num:
    text: str
    start: int
    end: int
    value: Decimal
    sig: int
    kind: str                # plain | percent | currency
    cur: Optional[str]
    plain_int: bool


def _round_sig(x: Decimal, s: int) -> Decimal:
    if x == 0:
        return x
    try:
        return x.quantize(Decimal(1).scaleb(x.adjusted() - s + 1), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return x


def _exp(v: Decimal) -> int:
    return v.adjusted() if v != 0 else 0


def _parse(m: "re.Match") -> Optional[Num]:
    raw = m.group("num")
    try:
        val = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    suf = (m.group("suf") or "").strip().lower()
    cur = (m.group("cur") or "").strip().lower()
    cur_code = _CUR.get(cur) if cur else None
    kind, mult = "plain", 1
    if suf in ("%", "percent"):
        kind = "percent"
    else:
        mult = _MULT.get(suf, 1)
        if cur_code:
            kind = "currency"
    digits = raw.replace(",", "").replace(".", "").lstrip("0")
    return Num(
        text=m.group(0), start=m.start(), end=m.end(), value=val * mult, sig=max(1, len(digits)),
        kind=kind, cur=cur_code,
        plain_int=(not suf) and (not cur_code) and ("," not in raw) and ("." not in raw),
    )


def extract(text: str) -> List[Num]:
    return [n for n in (_parse(m) for m in _NUM.finditer(text)) if n]


def _blank_noise(text: str) -> str:
    chars = list(text)
    for rx in _NOISE:
        for m in rx.finditer(text):
            for i in range(m.start(), m.end()):
                chars[i] = " "
    return "".join(chars)


def _compatible(out: Num, ev: Num) -> bool:
    if out.cur and ev.cur and out.cur != ev.cur:
        return False                                   # $500 vs INR 500
    if out.kind == "percent" and ev.kind != "percent":
        return False
    if ev.kind == "percent" and out.kind not in ("percent", "plain"):
        return False
    if ev.value == out.value:
        return True
    # Output may be a ROUNDED form of the evidence ("1.2M" from 1,234,567), never more precise than it.
    return _round_sig(ev.value, out.sig) == out.value


class Evidence:
    """All numbers found in the evidence texts, bucketed by order of magnitude for fast lookup."""

    def __init__(self, texts: Iterable[str]):
        self.nums: List[Num] = []
        self._b: Dict[int, List[Num]] = {}
        for t in texts:
            for n in extract(t or ""):
                self.nums.append(n)
                self._b.setdefault(_exp(n.value), []).append(n)

    def find(self, out: Num) -> Optional[Num]:
        e = _exp(out.value)
        for k in (e, e - 1, e + 1):
            for ev in self._b.get(k, ()):
                if _compatible(out, ev):
                    return ev
        return None

    def derive(self, out: Num, max_vals: int = 60) -> bool:
        """Is `out` a simple arithmetic combination (a+b, a-b, a*b, a/b, % change) of two evidence numbers?"""
        vals, seen = [], set()
        for n in self.nums:
            if n.value not in seen:
                seen.add(n.value)
                vals.append(n.value)
            if len(vals) >= max_vals:
                break
        for a in vals:
            for b in vals:
                cands = [a + b, a - b, a * b]
                if b != 0:
                    cands += [a / b, (a - b) / b * 100]
                for c in cands:
                    if c != 0 and _round_sig(abs(c), out.sig) == out.value:
                        return True
        return False


@dataclass
class Finding:
    text: str
    start: int
    end: int
    value: Decimal
    status: str  # grounded | derived | ungrounded | ignored


@dataclass
class Report:
    findings: List[Finding]

    def ungrounded(self) -> List[Finding]:
        return [f for f in self.findings if f.status == "ungrounded"]

    @property
    def ok(self) -> bool:
        return not self.ungrounded()


class NumericFirewall:
    def __init__(self, action: str = "tag", ignore_ints_up_to: int = 10, allow_years: bool = True,
                 allow_derived: bool = False, tag: str = " [UNVERIFIED]"):
        if action not in ("tag", "redact", "block"):
            raise ValueError("action must be tag, redact or block")
        self.action, self.ignore_ints_up_to = action, ignore_ints_up_to
        self.allow_years, self.allow_derived, self.tag = allow_years, allow_derived, tag

    def check(self, output: str, evidence: Evidence) -> Report:
        findings: List[Finding] = []
        for n in extract(_blank_noise(output)):
            if n.plain_int and n.value <= self.ignore_ints_up_to:
                status = "ignored"
            elif self.allow_years and n.plain_int and 1990 <= n.value <= 2100 and len(n.text) == 4:
                status = "ignored"
            elif evidence.find(n):
                status = "grounded"
            elif self.allow_derived and evidence.derive(n):
                status = "derived"
            else:
                status = "ungrounded"
            findings.append(Finding(n.text, n.start, n.end, n.value, status))
        return Report(findings)

    def enforce(self, output: str, evidence: Evidence):
        """Return (possibly modified text, report). Raises UngroundedNumber when action='block'."""
        rep = self.check(output, evidence)
        bad = rep.ungrounded()
        if not bad:
            return output, rep
        if self.action == "block":
            raise UngroundedNumber(rep)
        out = output
        for f in sorted(bad, key=lambda f: -f.start):  # edit from the end so earlier offsets stay valid
            if self.action == "redact":
                out = out[:f.start] + "[number removed]" + out[f.end:]
            else:
                out = out[:f.end] + self.tag + out[f.end:]
        return out, rep
