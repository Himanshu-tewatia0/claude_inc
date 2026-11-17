import os
import json
import time
import sys

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
        """Runs BEFORE any LLM call to block runaway loops or budget exhaustion."""
        now = time.time()
        
        # 1. Clean up old timestamps (rate window: 60 seconds)
        self.call_timestamps = [t for t in self.call_timestamps if now - t < 60]
        
        # 2. Check Velocity / Rapid-Fire Loop
        if len(self.call_timestamps) >= self.max_calls_per_minute:
            raise RuntimeError(
                f"[CLAUDE_INC FIREWALL] HALTED: Velocity limit hit! "
                f"Agent attempted {len(self.call_timestamps)} calls in 60s (Possible Infinite Loop)."
            )
            
        # 3. Check Hard Budget Limit
        if self.total_spent + estimated_cost_usd > self.max_budget_usd:
            raise PermissionError(
                f"[CLAUDE_INC FIREWALL] BLOCKED: Budget limit reached! "
                f"Spent: ${self.total_spent:.4f} / Limit: ${self.max_budget_usd:.4f}"
            )
            
        # Record this call attempt
        self.call_timestamps.append(now)
        print(f"[CLAUDE_INC] Check Passed. Current Spend: ${self.total_spent:.4f} / ${self.max_budget_usd:.4f}")

    def log_spend(self, actual_cost_usd):
        """Logs actual spend after a successful LLM API execution."""
        self.total_spent += actual_cost_usd
        self._save_ledger()
        print(f"[CLAUDE_INC] Logged ${actual_cost_usd:.4f}. Total Cumulative Spend: ${self.total_spent:.4f}")

# --- SELF-TEST HARNESS ---
if __name__ == "__main__":
    print("Initializing Claude-Inc Firewall SDK...")
    firewall = AgentFirewall(max_budget_usd=0.05, max_calls_per_minute=3)
    
    try:
        print("\nTest 1: Normal pre-flight check...")
        firewall.pre_flight_check(0.01)
        firewall.log_spend(0.01)
        
        print("\nTest 2: Simulating rapid loop attack...")
        firewall.pre_flight_check(0.01)
        firewall.pre_flight_check(0.01)
        firewall.pre_flight_check(0.01) # Should trigger velocity block
    except Exception as e:
        print(f"\n[CAUGHT EXCEPTION - FIREWALL WORKING]: {e}")
