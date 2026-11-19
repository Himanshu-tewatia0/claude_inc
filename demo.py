"""agentfuse demo. No network, no API key, Rs0: a FAKE provider stands in for the real one.
Run:  python demo.py
"""
import random
import string

from agentfuse import BudgetExceeded, FuseError, Guard, Limit, LoopDetected, Policy
from agentfuse.estimate import estimate_input_tokens

MODEL = "claude-sonnet-5-5"  # list price used for the arithmetic: $2 in / $10 out per million tokens
random.seed(7)


class Blk:
    def __init__(self, text):
        self.type, self.text = "text", text


class Usage:
    def __init__(self, i, o):
        self.input_tokens, self.output_tokens = i, o


class Resp:
    def __init__(self, text, i, o):
        self.content, self.usage = [Blk(text)], Usage(i, o)


class FakeProvider:
    """Each reply is 800 tokens long; the 'agent' feeds it back, so the context grows every call."""

    def __init__(self, reply=None):
        self.calls, self.reply = 0, reply

    def create(self, **kw):
        self.calls += 1
        tokens_in = estimate_input_tokens(kw["messages"], kw.get("system"))
        return Resp(self.reply or "".join(random.choices(string.ascii_lowercase + " ", k=2400)), tokens_in, 800)


def cost(i, o):
    return (i * 2 + o * 10) / 1e6


def agent_loop(call, max_steps=200):
    """A runaway agent: keeps appending the last answer and asking again."""
    messages = [{"role": "user", "content": "Plan the project and keep refining the plan."}]
    for step in range(1, max_steps + 1):
        res = call(messages)
        text = res.text if hasattr(res, "text") else res.content[0].text
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": "Refine again: " + "".join(random.choices(string.ascii_lowercase, k=12))})
    return step


print("=" * 66)
print("SCENE 1: a runaway agent, no guard")
prov = FakeProvider()
total = 0.0


def unguarded(messages):
    global total
    r = prov.create(model=MODEL, messages=messages, max_tokens=1000)
    total += cost(r.usage.input_tokens, r.usage.output_tokens)
    return r


agent_loop(unguarded, max_steps=200)
print(f"  200 calls made, real spend ${total:.2f}  (nothing stopped it)")

print("\nSCENE 2: the same agent behind a $1.00 per-run budget")
prov = FakeProvider()
guard = Guard(Policy(limits=[Limit("run", "run", 1.00)]))
try:
    with guard.run(run_id="demo-run", user_id="alice"):
        agent_loop(lambda m: guard.call(prov.create, model=MODEL, messages=m, max_tokens=1000), max_steps=200)
except BudgetExceeded as e:
    spent = guard.ledger.spent_micro("run", "demo-run", "run") / 1e6
    print(f"  stopped after {prov.calls} calls, spend recorded ${spent:.4f}")
    print(f"  reason: {e}")
    print("  the call that would have broken the limit was never sent to the provider")

print("\n" + "=" * 66)
print("SCENE 3: an agent stuck repeating the same request (cheap, but endless)")
prov = FakeProvider(reply="Still working on it.")
guard = Guard(Policy(limits=[]))
try:
    with guard.run(run_id="stuck"):
        for _ in range(50):
            guard.call(prov.create, model=MODEL, messages=[{"role": "user", "content": "Retry step 1"}], max_tokens=100)
except LoopDetected as e:
    print(f"  stopped after {prov.calls} provider calls: {e}")
try:
    with guard.run(run_id="stuck"):
        guard.call(prov.create, model=MODEL, messages=[{"role": "user", "content": "something new"}], max_tokens=100)
except FuseError as e:
    print(f"  breaker is open, so even a new request is refused: {type(e).__name__}")

print("\n" + "=" * 66)
print("SCENE 4: numeric firewall (checks that numbers TRACE to the evidence; it does not check truth)")
prov = FakeProvider(reply="Q3 revenue was Rs 12.35 lakh, up 18% from Q2, with churn at 7.5% and 412 new customers.")
guard = Guard(Policy(limits=[], numeric_action="tag"))
evidence = ["Q3 revenue: 1,234,567 rupees. Q2 revenue: 1,046,000 rupees.", "Churn rate this quarter: 4.2%"]
res = guard.call(prov.create, model=MODEL, messages=[{"role": "user", "content": "Summarise Q3."}],
                 max_tokens=200, evidence=evidence)
print("  model said :", prov.reply)
print("  released   :", res.text)
for f in res.report.findings:
    print(f"    {f.text:>12s} -> {f.status}")
print("=" * 66)
print("Note: '18%' is flagged because it is a derived figure, not a quoted one. Turn on allow_derived=True")
print("to accept simple arithmetic on evidence numbers. '412 new customers' and '7.5%' appear nowhere in the evidence.")
