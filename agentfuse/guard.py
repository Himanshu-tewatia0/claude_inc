"""Guard: the pipeline every model call goes through.

  breaker check -> loop check -> clamp max_tokens -> estimate worst case -> RESERVE (atomic)
  -> provider call -> SETTLE real cost -> numeric firewall -> audit

Everything before the provider call happens locally in microseconds and can stop the call.
"""
from __future__ import annotations

import contextvars
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from .audit import AuditLog
from .errors import BudgetExceeded, CircuitOpen, FuseError, LoopDetected, UngroundedNumber
from .estimate import estimate_input_tokens, flatten, message_text
from .ledger import Ledger, Limit, to_micro
from .loop import LoopDetector, signature
from .pricing import PriceBook
from .truth import Evidence, NumericFirewall, Report

_ctx: "contextvars.ContextVar[Optional[_Run]]" = contextvars.ContextVar("agentfuse_run", default=None)


@dataclass
class Policy:
    limits: List[Limit] = field(default_factory=list)
    default_max_tokens: int = 1024        # injected when the caller forgets max_tokens
    max_tokens_cap: Optional[int] = None  # hard ceiling on any single call's output
    loop_window: int = 20
    loop_repeat_limit: int = 3
    max_steps_per_run: int = 100
    max_wall_s_per_run: float = 900.0
    breaker_cooldown_s: float = 300.0
    numeric_action: Optional[str] = "tag"  # "tag" | "redact" | "block" | None (firewall off)
    allow_derived: bool = False
    audit_path: Optional[str] = None
    ledger_path: Optional[str] = None     # None = in-memory (lost on exit); set a file path to persist


class _Run:
    def __init__(self, run_id: str, user_id: str, org_id: str):
        self.run_id, self.user_id, self.org_id = run_id, user_id, org_id
        self._tok = None

    def __enter__(self):
        self._tok = _ctx.set(self)
        return self

    def __exit__(self, *exc):
        _ctx.reset(self._tok)
        return False


@dataclass
class GuardedResult:
    raw: Any
    text: str
    report: Optional[Report]
    cost_usd: float
    reserved_usd: float
    call_id: str

    def __getattr__(self, name):  # fall through to the provider response (.content, .usage, ...)
        if name == "raw":
            raise AttributeError(name)
        return getattr(self.raw, name)


def _g(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _response_text(resp: Any) -> str:
    content = _g(resp, "content")
    if isinstance(content, list):  # Anthropic-style blocks
        return "".join((_g(b, "text", "") or "") for b in content if _g(b, "type") == "text")
    choices = _g(resp, "choices")  # OpenAI-style
    if choices:
        return _g(_g(choices[0], "message"), "content", "") or ""
    return ""


def _response_usage(resp: Any):
    u = _g(resp, "usage")
    i = _g(u, "input_tokens")
    if i is None:
        i = _g(u, "prompt_tokens", 0)
    o = _g(u, "output_tokens")
    if o is None:
        o = _g(u, "completion_tokens", 0)
    searches = _g(_g(u, "server_tool_use"), "web_search_requests", 0) or 0
    return int(i or 0), int(o or 0), int(searches)


class Guard:
    def __init__(self, policy: Policy, ledger: Optional[Ledger] = None, prices: Optional[PriceBook] = None,
                 clock: Callable[[], float] = time.time):
        self.policy = policy
        self.ledger = ledger or Ledger(policy.ledger_path or ":memory:", clock=clock)
        self.prices = prices or PriceBook()
        self.loop = LoopDetector(policy.loop_window, policy.loop_repeat_limit, policy.max_steps_per_run,
                                 policy.max_wall_s_per_run, clock)
        self.firewall = (NumericFirewall(policy.numeric_action, allow_derived=policy.allow_derived)
                         if policy.numeric_action else None)
        self.audit = AuditLog(policy.audit_path) if policy.audit_path else None
        self._default = _Run("default", "anonymous", "default")

    # ---- context ----
    def run(self, run_id: Optional[str] = None, user_id: str = "anonymous", org_id: str = "default") -> _Run:
        return _Run(run_id or uuid.uuid4().hex[:12], user_id, org_id)

    def _log(self, **rec) -> None:
        if self.audit:
            self.audit.write(rec)

    # ---- the pipeline ----
    def call(self, fn: Callable[..., Any], *, model: str, messages: list, system: Any = None,
             tools: Optional[list] = None, max_tokens: Optional[int] = None, searches: int = 0,
             evidence: Optional[List[str]] = None, max_tokens_param: str = "max_tokens",
             **kwargs) -> GuardedResult:
        if kwargs.get("stream"):
            # Streamed usage cannot be accounted for in this version. Refusing is safer than recording $0.
            raise FuseError("streaming is not supported in this version; call without stream=True")
        run = _ctx.get() or self._default
        base = {"run": run.run_id, "user": run.user_id, "model": model}

        # 1. circuit breakers
        for key in (f"run:{run.run_id}", f"user:{run.user_id}", "global"):
            reason = self.ledger.breaker(key, self.policy.breaker_cooldown_s)
            if reason:
                self._log(event="blocked", why="circuit_open", key=key, **base)
                raise CircuitOpen(f"{key}: {reason}")

        # 2. bound the output
        mt = int(max_tokens or self.policy.default_max_tokens)
        if self.policy.max_tokens_cap:
            mt = min(mt, self.policy.max_tokens_cap)

        # 3. loop / runaway check
        tail = "\n".join(message_text(m)[0] for m in messages[-2:])
        names = [t.get("name", "") for t in (tools or []) if isinstance(t, dict)]
        try:
            self.loop.check(run.run_id, signature(model, tail, names))
        except LoopDetected as e:
            self.ledger.trip(f"run:{run.run_id}", str(e))
            self._log(event="blocked", why="loop", detail=str(e), **base)
            raise

        # 4. worst-case cost, then atomic reserve
        price = self.prices.get(model)  # raises UnknownModel (fail closed)
        in_est = estimate_input_tokens(messages, system, tools)
        est_micro = to_micro(price.cost_usd(in_est, mt, searches))
        try:
            call_id = self.ledger.reserve(run_id=run.run_id, user_id=run.user_id, org_id=run.org_id,
                                          est_micro=est_micro, limits=self.policy.limits, model=model)
        except BudgetExceeded as e:
            self._log(event="blocked", why="budget", detail=str(e), est_usd=est_micro / 1e6, **base)
            raise

        # 5. the provider call (the ONLY line that spends money)
        payload = dict(model=model, messages=messages, **{max_tokens_param: mt}, **kwargs)
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools
        try:
            resp = fn(**payload)
        except BaseException as e:
            self.ledger.release(call_id)
            self._log(event="error", why=type(e).__name__, est_usd=est_micro / 1e6, **base)
            raise

        # 6. settle the real cost
        in_a, out_a, srch = _response_usage(resp)
        actual_micro = to_micro(price.cost_usd(in_a, out_a, srch))
        over = self.ledger.settle(call_id, actual_micro)

        # 7. numeric firewall
        text = _response_text(resp)
        report: Optional[Report] = None
        if self.firewall is not None:
            texts = [flatten(system)[0]] + [message_text(m)[0] for m in messages] + list(evidence or [])
            try:
                text, report = self.firewall.enforce(text, Evidence(texts))
            except UngroundedNumber:
                self._log(event="blocked_output", why="ungrounded_number", est_usd=est_micro / 1e6,
                          actual_usd=actual_micro / 1e6, **base)
                raise
        self._log(event="allowed", est_usd=est_micro / 1e6, actual_usd=actual_micro / 1e6,
                  underestimated=over > 0, ungrounded=len(report.ungrounded()) if report else 0, **base)
        return GuardedResult(raw=resp, text=text, report=report, cost_usd=actual_micro / 1e6,
                             reserved_usd=est_micro / 1e6, call_id=call_id)

    # ---- provider adapters ----
    def wrap_anthropic(self, client: Any) -> "WrappedAnthropic":
        return WrappedAnthropic(self, client)

    def wrap_openai(self, client: Any, max_tokens_param: str = "max_tokens") -> "WrappedOpenAI":
        """For OpenAI-style clients (client.chat.completions.create). Newer OpenAI models want
        max_tokens_param='max_completion_tokens'. Register prices first: PriceBook.register(...)."""
        return WrappedOpenAI(self, client, max_tokens_param)


class _Messages:
    def __init__(self, guard: Guard, client: Any):
        self._guard, self._client = guard, client

    def create(self, **kw) -> GuardedResult:
        return self._guard.call(self._client.messages.create, **kw)


class WrappedAnthropic:
    """Use exactly like anthropic.Anthropic(), but .messages.create() returns a GuardedResult.
    Tip: build the client with max_retries=0 so hidden SDK retries cannot spend outside the ledger."""

    def __init__(self, guard: Guard, client: Any):
        self.messages = _Messages(guard, client)
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)


class _OpenAIChat:
    def __init__(self, guard: Guard, client: Any, max_tokens_param: str):
        self._guard, self._client, self._param = guard, client, max_tokens_param

    def create(self, **kw) -> GuardedResult:
        mt = kw.pop("max_completion_tokens", None)
        mt = kw.pop("max_tokens", None) if mt is None else mt
        return self._guard.call(self._client.chat.completions.create, max_tokens=mt,
                                max_tokens_param=self._param, **kw)


class WrappedOpenAI:
    """Use like an OpenAI client, but .chat.completions.create() returns a GuardedResult."""

    def __init__(self, guard: Guard, client: Any, max_tokens_param: str = "max_tokens"):
        chat = type("chat", (), {})()
        chat.completions = _OpenAIChat(guard, client, max_tokens_param)
        self.chat = chat
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)
