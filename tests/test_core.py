import io
import contextlib
import json
import os
import tempfile
import threading
import unittest

from agentfuse import (AuditLog, BudgetExceeded, CircuitOpen, Evidence, FuseError, Guard, Ledger, Limit,
                       LoopDetected, NumericFirewall, Policy, Price, PriceBook, UngroundedNumber,
                       UnknownModel, to_micro, verify_chain)
from agentfuse.cli import main as cli_main
from agentfuse.estimate import estimate_input_tokens
from agentfuse.loop import LoopDetector, signature

KW = dict(run_id="r1", user_id="u1", org_id="o1")


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ---- fake provider (Anthropic-shaped) ----
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
    def __init__(self, reply="ok", i=1000, o=500, fail=None):
        self.reply, self.i, self.o, self.fail, self.calls, self.last = reply, i, o, fail, 0, None

    def create(self, **kw):
        self.calls += 1
        self.last = kw
        if self.fail:
            raise self.fail
        return Resp(self.reply, self.i, self.o)


MSG = [{"role": "user", "content": "hi"}]
CHEAP = PriceBook({"m": Price(1000.0, 1000.0)})  # $0.001 per token: easy arithmetic


class LedgerTests(unittest.TestCase):
    def test_reserve_blocks_before_any_spend(self):
        led = Ledger()
        lim = [Limit("global", "all", 0.10)]
        led.reserve(est_micro=to_micro(0.06), limits=lim, **KW)
        with self.assertRaises(BudgetExceeded):
            led.reserve(est_micro=to_micro(0.06), limits=lim, **KW)

    def test_settle_frees_unused_headroom(self):
        led = Ledger()
        lim = [Limit("global", "all", 0.10)]
        cid = led.reserve(est_micro=to_micro(0.09), limits=lim, **KW)
        led.settle(cid, to_micro(0.01))
        led.reserve(est_micro=to_micro(0.08), limits=lim, **KW)  # fits only because the first call settled low

    def test_overspend_is_recorded_not_hidden(self):
        led = Ledger()
        cid = led.reserve(est_micro=to_micro(0.01), limits=[], **KW)
        over = led.settle(cid, to_micro(0.05))
        self.assertEqual(over, to_micro(0.05) - to_micro(0.01))
        self.assertEqual(led.spent_micro("global", None, "all"), to_micro(0.05))

    def test_rolling_window_velocity(self):
        clk = Clock()
        led = Ledger(clock=clk)
        lim = [Limit("global", "minute", 0.05)]
        for _ in range(2):
            led.settle(led.reserve(est_micro=to_micro(0.02), limits=lim, **KW), to_micro(0.02))
        with self.assertRaises(BudgetExceeded):
            led.reserve(est_micro=to_micro(0.02), limits=lim, **KW)
        clk.t += 61
        led.reserve(est_micro=to_micro(0.02), limits=lim, **KW)

    def test_stale_reservation_expires(self):
        clk = Clock()
        led = Ledger(clock=clk, reservation_ttl_s=300)
        lim = [Limit("global", "all", 0.05)]
        led.reserve(est_micro=to_micro(0.05), limits=lim, **KW)  # crashed process never settles
        with self.assertRaises(BudgetExceeded):
            led.reserve(est_micro=to_micro(0.01), limits=lim, **KW)
        clk.t += 301
        led.reserve(est_micro=to_micro(0.01), limits=lim, **KW)

    def test_scopes_are_isolated(self):
        led = Ledger()
        lim = [Limit("user", "all", 0.05)]
        led.reserve(run_id="r", user_id="alice", org_id="o", est_micro=to_micro(0.05), limits=lim)
        led.reserve(run_id="r", user_id="bob", org_id="o", est_micro=to_micro(0.05), limits=lim)
        with self.assertRaises(BudgetExceeded):
            led.reserve(run_id="r", user_id="alice", org_id="o", est_micro=to_micro(0.01), limits=lim)

    def test_atomic_across_connections_and_threads(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "l.db")
            lim = [Limit("global", "all", 1.00)]
            ledgers = [Ledger(path) for _ in range(8)]
            results, lock = [], threading.Lock()

            def worker(led):
                for _ in range(5):
                    try:
                        led.reserve(est_micro=to_micro(0.10), limits=lim, **KW)
                        r = 1
                    except BudgetExceeded:
                        r = 0
                    with lock:
                        results.append(r)

            ts = [threading.Thread(target=worker, args=(L,)) for L in ledgers]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(sum(results), 10)  # 40 attempts, exactly $1.00 / $0.10 allowed
            [L.close() for L in ledgers]

    def test_breaker_open_cooldown_reset(self):
        clk = Clock()
        led = Ledger(clock=clk)
        led.trip("run:x", "loop")
        self.assertEqual(led.breaker("run:x", 300), "loop")
        clk.t += 301
        self.assertIsNone(led.breaker("run:x", 300))
        led.trip("run:y", "loop")
        led.reset("run:y")
        self.assertIsNone(led.breaker("run:y", 300))

    def test_summary_is_computed_from_data(self):
        led = Ledger()
        led.settle(led.reserve(est_micro=to_micro(0.02), limits=[], **KW), to_micro(0.02))
        s = led.summary()
        self.assertAlmostEqual(s["all"]["usd"], 0.02, places=6)
        self.assertEqual(s["all"]["calls"], 1)


class PricingEstimateTests(unittest.TestCase):
    def test_unknown_model_fails_closed(self):
        with self.assertRaises(UnknownModel):
            PriceBook().get("some-unknown-model")

    def test_prefix_match(self):
        self.assertEqual(PriceBook().get("claude-sonnet-5-5-20261101").out_per_mtok, 10.0)

    def test_estimate_is_an_upper_bound(self):
        ascii_text = "x" * 4000                  # real tokenizers: ~1000 tokens
        hindi = "नमस्ते दुनिया " * 200            # non-ASCII: many tokens per word
        self.assertGreaterEqual(estimate_input_tokens([{"content": ascii_text}]), 1000)
        self.assertGreaterEqual(estimate_input_tokens([{"content": hindi}]), len(hindi))

    def test_images_and_tools_are_counted(self):
        base = estimate_input_tokens([{"content": "hi"}])
        img = estimate_input_tokens([{"content": [{"type": "image", "source": {}}]}])
        tools = estimate_input_tokens([{"content": "hi"}], tools=[{"name": "t", "description": "x" * 300}])
        self.assertGreater(img, base + 1000)
        self.assertGreater(tools, base + 50)


class LoopTests(unittest.TestCase):
    def test_same_request_trips_on_third_repeat(self):
        d = LoopDetector(repeat_limit=3)
        s = signature("m", "same prompt")
        d.check("r", s)
        d.check("r", s)
        with self.assertRaises(LoopDetected):
            d.check("r", s)

    def test_changing_counter_is_still_a_loop(self):
        self.assertEqual(signature("m", "retry attempt 1"), signature("m", "retry attempt 2"))

    def test_different_requests_do_not_trip(self):
        d = LoopDetector(repeat_limit=3)
        for i in range(10):
            d.check("r", signature("m", "unique prompt " + "ab" * i + chr(97 + i)))

    def test_step_ceiling(self):
        d = LoopDetector(max_steps=3, repeat_limit=99)
        for i in range(3):
            d.check("r", signature("m", chr(97 + i) * (i + 1)))
        with self.assertRaises(LoopDetected):
            d.check("r", signature("m", "zzz"))


class NumericFirewallTests(unittest.TestCase):
    def setUp(self):
        self.fw = NumericFirewall(action="tag")

    def statuses(self, output, *evidence, fw=None):
        fw = fw or self.fw
        rep = fw.check(output, Evidence(evidence))
        return [(f.text, f.status) for f in rep.findings]

    def test_exact_number_is_grounded(self):
        self.assertEqual(self.statuses("Revenue was 1,234,567.", "total revenue 1,234,567"), [("1,234,567", "grounded")])

    def test_rounded_form_is_grounded_but_wrong_rounding_is_not(self):
        self.assertEqual(self.statuses("About 1.2M.", "revenue 1,234,567")[0][1], "grounded")
        self.assertEqual(self.statuses("About 1.3M.", "revenue 1,234,567")[0][1], "ungrounded")

    def test_never_more_precise_than_evidence(self):
        self.assertEqual(self.statuses("Exactly 1,234,567.", "roughly 1.2M")[0][1], "ungrounded")

    def test_indian_formats_and_lakh_crore(self):
        self.assertEqual(self.statuses("₹12,34,567 collected", "collected 1234567")[0][1], "grounded")
        self.assertEqual(self.statuses("about 12.35 lakh", "1,234,567")[0][1], "grounded")
        self.assertEqual(self.statuses("3.5 crore", "35,000,000")[0][1], "grounded")

    def test_currency_mismatch_is_caught(self):
        self.assertEqual(self.statuses("It cost $500.", "the fee was ₹500")[0][1], "ungrounded")

    def test_percent_does_not_match_a_money_amount(self):
        self.assertEqual(self.statuses("Churn was 12%.", "the plan costs $12")[0][1], "ungrounded")
        self.assertEqual(self.statuses("Churn was 12%.", "churn was 12%")[0][1], "grounded")

    def test_noise_is_ignored(self):
        out = "1. Summary\n- point\nOn 14 Nov 2026 and 2026-11-14, version 2 of 3 items in 2026."
        found = self.statuses(out)
        self.assertTrue(found)  # the small ints and the year are still seen, just ignored
        self.assertTrue(all(status == "ignored" for _, status in found))  # dates and list markers were blanked

    def test_ungrounded_number_is_flagged(self):
        self.assertEqual(self.statuses("We lost 47 customers.", "we lost customers")[0][1], "ungrounded")

    def test_small_integers_ignored_by_default_but_configurable(self):
        self.assertEqual(self.statuses("We lost 7 customers.", "no numbers here")[0][1], "ignored")
        strict = NumericFirewall(ignore_ints_up_to=0)
        self.assertEqual(self.statuses("We lost 7 customers.", "no numbers here", fw=strict)[0][1], "ungrounded")

    def test_derived_percent_change(self):
        out, ev = "That is up 25% from before.", "last month 120 and this month 150"
        self.assertEqual(self.statuses(out, ev)[0][1], "ungrounded")
        self.assertEqual(self.statuses(out, ev, fw=NumericFirewall(allow_derived=True))[0][1], "derived")

    def test_actions(self):
        ev = Evidence(["revenue was 1,000"])
        text, rep = NumericFirewall("tag").enforce("Revenue 1,000 and profit 777.", ev)
        self.assertEqual(text, "Revenue 1,000 and profit 777 [UNVERIFIED].")
        text, _ = NumericFirewall("redact").enforce("Revenue 1,000 and profit 777.", ev)
        self.assertEqual(text, "Revenue 1,000 and profit [number removed].")
        with self.assertRaises(UngroundedNumber):
            NumericFirewall("block").enforce("Revenue 1,000 and profit 777.", ev)
        clean, rep = NumericFirewall("block").enforce("Revenue 1,000.", ev)
        self.assertTrue(rep.ok)


class GuardTests(unittest.TestCase):
    def guard(self, **policy):
        policy.setdefault("limits", [])
        return Guard(Policy(**policy), prices=CHEAP)

    def call(self, g, prov, **kw):
        kw.setdefault("model", "m")
        kw.setdefault("messages", MSG)
        kw.setdefault("max_tokens", 10)
        return g.call(prov.create, **kw)

    def test_budget_blocks_before_the_provider_is_called(self):
        g = self.guard(limits=[Limit("run", "run", 0.01)])  # one call's worst case is ~$0.019
        prov = FakeProvider(i=9, o=10)
        with self.assertRaises(BudgetExceeded):
            self.call(g, prov)
        self.assertEqual(prov.calls, 0)

    def test_real_cost_is_settled_from_usage(self):
        g = self.guard()
        res = self.call(g, FakeProvider(i=9, o=10))
        self.assertAlmostEqual(res.cost_usd, 0.019, places=9)
        self.assertAlmostEqual(g.ledger.spent_micro("global", None, "all") / 1e6, 0.019, places=9)

    def test_anthropic_prices_use_real_usage(self):
        g = Guard(Policy(limits=[]))
        res = g.call(FakeProvider(i=1000, o=500).create, model="claude-sonnet-5-5", messages=MSG, max_tokens=500)
        self.assertAlmostEqual(res.cost_usd, 0.007, places=9)  # (1000*2 + 500*10) / 1e6

    def test_max_tokens_injected_and_capped(self):
        g = self.guard(default_max_tokens=7, max_tokens_cap=20)
        prov = FakeProvider(i=9, o=7)
        self.call(g, prov, max_tokens=None)
        self.assertEqual(prov.last["max_tokens"], 7)
        self.call(g, prov, max_tokens=500, messages=[{"role": "user", "content": "another"}])
        self.assertEqual(prov.last["max_tokens"], 20)

    def test_hallucinated_number_is_tagged(self):
        g = self.guard()
        res = self.call(g, FakeProvider(reply="Revenue was 1,000 and churn 7.5%."),
                        messages=[{"role": "user", "content": "revenue was 1,000"}])
        self.assertIn("7.5% [UNVERIFIED]", res.text)
        self.assertNotIn("1,000 [UNVERIFIED]", res.text)
        self.assertFalse(res.report.ok)

    def test_block_mode_raises_but_money_was_already_settled(self):
        g = self.guard(numeric_action="block")
        with self.assertRaises(UngroundedNumber):
            self.call(g, FakeProvider(reply="Profit was 98,765."))
        self.assertGreater(g.ledger.spent_micro("global", None, "all"), 0)

    def test_streaming_is_refused_not_recorded_as_zero(self):
        g = self.guard()
        with self.assertRaises(FuseError):
            self.call(g, FakeProvider(), stream=True)

    def test_provider_error_releases_the_reservation(self):
        g = self.guard()
        with self.assertRaises(RuntimeError):
            self.call(g, FakeProvider(fail=RuntimeError("boom")))
        self.assertEqual(g.ledger.spent_micro("global", None, "all"), 0)

    def test_unknown_model_blocks_before_the_call(self):
        g = Guard(Policy(limits=[]))
        prov = FakeProvider()
        with self.assertRaises(UnknownModel):
            g.call(prov.create, model="gpt-unregistered", messages=MSG, max_tokens=5)
        self.assertEqual(prov.calls, 0)

    def test_loop_trips_breaker_and_next_call_is_refused(self):
        g = self.guard(loop_repeat_limit=3)
        prov = FakeProvider(i=9, o=10)
        with g.run(run_id="loopy"):
            self.call(g, prov)
            self.call(g, prov)
            with self.assertRaises(LoopDetected):
                self.call(g, prov)
            with self.assertRaises(CircuitOpen):
                self.call(g, prov, messages=[{"role": "user", "content": "a different prompt entirely"}])
        self.assertEqual(prov.calls, 2)

    def test_user_budgets_are_independent(self):
        g = self.guard(limits=[Limit("user", "all", 0.03)])
        prov = FakeProvider(i=9, o=10)
        with g.run(user_id="alice"):
            self.call(g, prov)
            with self.assertRaises(BudgetExceeded):
                self.call(g, prov, messages=[{"role": "user", "content": "second"}])
        with g.run(user_id="bob"):
            self.call(g, prov, messages=[{"role": "user", "content": "third"}])

    def test_openai_adapter(self):
        class Msg:
            content = "total 1,000"

        class Choice:
            message = Msg()

        class U:
            prompt_tokens, completion_tokens = 9, 10

        class R:
            choices, usage = [Choice()], U()

        class Completions:
            def __init__(self):
                self.kw = None

            def create(self, **kw):
                self.kw = kw
                return R()

        class Chat:
            completions = Completions()

        class Client:
            chat = Chat()

        g = self.guard()
        client = g.wrap_openai(Client(), max_tokens_param="max_completion_tokens")
        res = client.chat.completions.create(model="m", messages=[{"role": "user", "content": "total 1,000"}],
                                             max_tokens=10)
        self.assertEqual(res.text, "total 1,000")
        self.assertAlmostEqual(res.cost_usd, 0.019, places=9)

    def test_audit_chain_detects_tampering_and_stores_no_prompts(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "audit.jsonl")
            g = self.guard(audit_path=path)
            self.call(g, FakeProvider(i=9, o=10), messages=[{"role": "user", "content": "SECRET-PROMPT-TEXT"}])
            self.call(g, FakeProvider(i=9, o=10), messages=[{"role": "user", "content": "second one"}])
            self.assertEqual(verify_chain(path), (True, None))
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
            self.assertNotIn("SECRET-PROMPT-TEXT", raw)
            lines = raw.splitlines()
            rec = json.loads(lines[0])
            rec["actual_usd"] = 0.0
            lines[0] = json.dumps(rec)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            self.assertEqual(verify_chain(path), (False, 1))


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli_main(list(argv))
        return code, buf.getvalue()

    def test_status_reports_real_numbers_and_no_green_checkmarks(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "l.db")
            g = Guard(Policy(limits=[], ledger_path=db), prices=CHEAP)
            g.call(FakeProvider(i=9, o=10).create, model="m", messages=MSG, max_tokens=10)
            g.ledger.close()
            code, out = self.run_cli("status", "--db", db)
            self.assertEqual(code, 0)
            self.assertIn("$0.0190", out)
            self.assertNotIn("ARMED", out)
            self.assertNotIn("Zero", out)

    def test_status_on_missing_ledger_says_so(self):
        code, out = self.run_cli("status", "--db", os.path.join(tempfile.gettempdir(), "definitely_missing.db"))
        self.assertEqual(code, 1)
        self.assertIn("No ledger", out)

    def test_corrupt_ledger_is_not_silently_forgotten(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "l.db")
            with open(db, "w") as fh:
                fh.write("this is not a database")
            with self.assertRaises(Exception):
                Ledger(db)  # fails loudly instead of restarting at $0


class PackagingTests(unittest.TestCase):
    def test_version_in_code_matches_pyproject(self):
        import re
        import agentfuse
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "pyproject.toml"), encoding="utf-8") as fh:
            declared = re.search(r'(?m)^version\s*=\s*"([^"]+)"', fh.read()).group(1)
        self.assertEqual(agentfuse.__version__, declared)

    def test_no_generic_top_level_cli_module_is_shipped(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertFalse(os.path.exists(os.path.join(here, "cli.py")))
        self.assertTrue(os.path.exists(os.path.join(here, "agentfuse", "cli.py")))


if __name__ == "__main__":
    unittest.main()
