# agentfuse

Pre-call spend limits, loop breakers, and a deterministic numeric check for LLM agent code.
Alpha (0.2.0). Python 3.9+, no dependencies. Every claim below is covered by a test in `tests/`. The tests use provider-shaped fake responses. The library has not yet been validated against live provider APIs, so please report any mismatch.

## What it does

- **Reserves the worst-case cost before each call.** It estimates the input tokens (deliberately high), adds `max_tokens` times the output price, and refuses the call if that would break any limit. The check runs in one SQLite write transaction, so threads and processes on one machine cannot jointly overspend.
- **Settles the real cost afterwards** from the usage the provider reports, and frees the unused headroom.
- **Limits by run, user, org, or globally**, over rolling windows (per run, minute, hour, day, month).
- **Injects and caps `max_tokens`** when the caller forgets it, so one call cannot be unbounded.
- **Detects loops.** The same request (digits masked, so a changing counter still matches) repeating 3 times in the last 20 calls, more than 100 calls in a run, or more than 15 minutes of wall time opens a circuit breaker for that run.
- **Refuses unknown models and streaming** instead of recording $0. Unknown price means the cost cannot be bounded, so the call is blocked.
- **Numeric firewall.** Every number in the output must appear in the evidence (your prompt, documents, tool results), allowing for rounding (`1.2M` from `1,234,567`) and Indian formats (`₹12,34,567`, `lakh`, `crore`). Violations are tagged `[UNVERIFIED]`, redacted, or blocked.
- **Tamper-evident audit log** (hash chain). It stores decisions, costs, and counts. It never stores prompts or outputs.

## What it does NOT do

- It is **not a network proxy**. Only calls made through the wrapper are guarded. Code that calls the provider directly is not.
- Token counts before the call are **estimates**. Settlement uses provider-reported usage. If you pass a wrong price, the accounting is wrong.
- The numeric firewall checks that numbers **trace to the evidence, not that they are true**. It cannot catch a correct number attached to the wrong subject, and it ignores integers up to 10 by default (configurable).
- **No streaming, no async** in this version.
- Prices for Anthropic models are bundled (checked 2026-11-14) and **will go stale**. For any other model you must register a price from the provider's pricing page.
- The ledger is a **single-machine** SQLite file. It is not distributed.
- It does not replace provider-side limits. Use both.

## Install

```
pip install agentfuse
```

## Quick start

```python
import anthropic
from agentfuse import Guard, Policy, Limit

guard = Guard(Policy(
    limits=[Limit("run", "run", 1.00), Limit("user", "day", 5.00), Limit("global", "minute", 20.00)],
    ledger_path="fuse.db",          # omit for an in-memory ledger that is lost on exit
    audit_path="fuse_audit.jsonl",
))
client = guard.wrap_anthropic(anthropic.Anthropic(max_retries=0))  # hidden SDK retries could spend outside the ledger

with guard.run(user_id="alice"):
    res = client.messages.create(model="claude-sonnet-5-5", max_tokens=500,
                                 messages=[{"role": "user", "content": "Summarise Q3."}])
print(res.text, res.cost_usd)   # res.raw is the provider's own response
```

OpenAI-style clients:

```python
import openai
from agentfuse import Price

guard.prices.register("YOUR-MODEL-ID", Price(in_per_mtok=0.0, out_per_mtok=0.0))  # copy real prices from the provider
client = guard.wrap_openai(openai.OpenAI(max_retries=0), max_tokens_param="max_completion_tokens")
```

## Stops you can catch

All inherit `FuseError`: `BudgetExceeded`, `LoopDetected`, `CircuitOpen`, `UnknownModel`, `UngroundedNumber`.

## Command line

```
agentfuse status --db fuse.db     # spend by window, open reservations, top users, open breakers
agentfuse verify fuse_audit.jsonl # check the audit hash chain
agentfuse reset --db fuse.db run:abc123
```

Every figure is computed from the ledger. The CLI never prints reassurance it cannot prove.

## Migrating from `claude-inc` 0.1.0

0.1.0 counted a flat $0.01 per call instead of the real cost, did not enforce the settings of a custom firewall when patching OpenAI, restarted from $0 on a corrupt ledger, and its CLI printed a fixed "all clear" message. `agentfuse` is a rewrite that fixes these, and each fix has a regression test.

## Development

```
python -m unittest discover -s tests -v
python demo.py
```

MIT licensed.
