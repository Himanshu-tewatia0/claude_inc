import os
import json
import time
import functools

class AgentFirewall:
    def __init__(self, max_budget_usd=5.00, max_calls_per_minute=10):
        self.max_budget_usd = max_budget_usd
        self.max_calls_per_minute = max_calls_per_minute
        self.total_spent = 0.0
        self.call_timestamps = []
        self.ledger_file = "claude_inc_ledger.json"
        self._load_ledger()

    def _load_ledger(self):
        if os.path.exists(self.ledger_file):
            try:
                data = json.load(open(self.ledger_file, "r"))
                self.total_spent = data.get("total_spent", 0.0)
            except Exception:
                pass

    def _save_ledger(self):
        json.dump({"total_spent": self.total_spent, "last_updated": time.time()}, open(self.ledger_file, "w"))

    def pre_flight_check(self, estimated_cost_usd=0.01):
        now = time.time()
        self.call_timestamps = [t for t in self.call_timestamps if now - t < 60]
        
        if len(self.call_timestamps) >= self.max_calls_per_minute:
            raise RuntimeError(
                f"[CLAUDE_INC FIREWALL] HALTED: Velocity limit hit! "
                f"Agent attempted {len(self.call_timestamps)} calls in 60s (Possible Infinite Loop)."
            )
            
        if self.total_spent + estimated_cost_usd > self.max_budget_usd:
            raise PermissionError(
                f"[CLAUDE_INC FIREWALL] BLOCKED: Budget limit reached! "
                f"Spent:  / Limit: "
            )
            
        self.call_timestamps.append(now)

    def log_spend(self, actual_cost_usd):
        self.total_spent += actual_cost_usd
        self._save_ledger()

    def guard(self, estimated_cost=0.01):
        """Decorator for zero-touch automatic function interception."""
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                self.pre_flight_check(estimated_cost)
                result = func(*args, **kwargs)
                self.log_spend(estimated_cost)
                return result
            return wrapper
        return decorator

# Global default instance for zero-touch imports
default_firewall = AgentFirewall()