import time
import functools
import os
from .ledger import AtomicLedger

class AgentFirewall:
    def __init__(self, max_budget_usd: float = 5.00, max_calls_per_minute: int = 10, max_retries: int = 2):
        self.max_budget_usd = max_budget_usd
        self.max_calls_per_minute = max_calls_per_minute
        self.max_retries = max_retries
        self.ledger = AtomicLedger()
        self.call_timestamps = []
        self._is_patched = False

    def pre_flight_check(self, estimated_cost_usd: float = 0.01):
        now = time.time()
        self.call_timestamps = [t for t in self.call_timestamps if now - t < 60]
        if len(self.call_timestamps) >= self.max_calls_per_minute:
            raise RuntimeError(f"[AGENTFUSE] HALTED: Velocity limit hit ({len(self.call_timestamps)} calls/60s).")
        data = self.ledger.load()
        total_spent = data.get("total_spent", 0.0)
        if total_spent + estimated_cost_usd > self.max_budget_usd:
            raise PermissionError(f"[AGENTFUSE] BLOCKED: Budget limit reached (${total_spent:.4f}/${self.max_budget_usd:.4f}).")
        self.call_timestamps.append(now)

    def log_spend(self, actual_cost_usd: float):
        data = self.ledger.load()
        data["total_spent"] = data.get("total_spent", 0.0) + actual_cost_usd
        self.ledger.save(data)

    def guard(self, estimated_cost: float = 0.01):
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                retries = 0
                while retries <= self.max_retries:
                    try:
                        self.pre_flight_check(estimated_cost)
                        res = func(*args, **kwargs)
                        self.log_spend(estimated_cost)
                        return res
                    except (ConnectionError, TimeoutError, IOError) as e:
                        retries += 1
                        if retries > self.max_retries:
                            raise RuntimeError(f"Max retries exceeded: {e}")
                        time.sleep(0.5 * (2 ** retries))
                    except (PermissionError, RuntimeError) as err:
                        raise err
            return wrapper
        return decorator

    def patch_openai(self, estimated_cost: float = 0.01):
        if self._is_patched:
            return
        try:
            import openai
            from openai.resources.chat.completions import Completions
            orig = Completions.create
            @functools.wraps(orig)
            def patched(self_client, *args, **kwargs):
                self.pre_flight_check(estimated_cost)
                res = orig(self_client, *args, **kwargs)
                self.log_spend(estimated_cost)
                return res
            Completions.create = patched
            self._is_patched = True
        except Exception:
            pass

_fw = AgentFirewall()
patch_openai = _fw.patch_openai
