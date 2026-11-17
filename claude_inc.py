"""
claude_inc.py
-------------
A local pre-execution AI agent spend firewall and token ledger.
Stops runaway LLM loops and budget overruns locally before hitting upstream servers.
"""

import os
import json
import time
import functools
from typing import Callable, Any, Optional

class AgentFirewall:
    """
    Tracks, inspects, and blocks autonomous agent LLM calls locally 
    based on strict budget caps, velocity limits, and transient error resilience.
    """
    
    def __init__(self, max_budget_usd: float = 5.00, max_calls_per_minute: int = 10, max_retries: int = 2) -> None:
        self.max_budget_usd = max_budget_usd
        self.max_calls_per_minute = max_calls_per_minute
        self.max_retries = max_retries
        self.total_spent = 0.0
        self.call_timestamps = []
        self.ledger_file = "claude_inc_ledger.json"
        self._load_ledger()

    def _load_ledger(self) -> None:
        """Loads accumulated spend history from local persistent storage."""
        if os.path.exists(self.ledger_file):
            try:
                with open(self.ledger_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.total_spent = data.get("total_spent", 0.0)
            except Exception:
                pass

    def _save_ledger(self) -> None:
        """Persists current total spend and timestamp to local disk."""
        try:
            with open(self.ledger_file, "w", encoding="utf-8") as f:
                json.dump({"total_spent": self.total_spent, "last_updated": time.time()}, f)
        except Exception:
            pass

    def pre_flight_check(self, estimated_cost_usd: float = 0.01) -> None:
        """
        Validates agent execution parameters before releasing upstream requests.
        Raises:
            RuntimeError: If velocity limits are breached (possible infinite loop).
            PermissionError: If total budget allocation has been exhausted.
        """
        now = time.time()
        # Filter timestamps to keep only the sliding 60-second window
        self.call_timestamps = [t for t in self.call_timestamps if now - t < 60]
        
        if len(self.call_timestamps) >= self.max_calls_per_minute:
            raise RuntimeError(
                f"[CLAUDE_INC FIREWALL] HALTED: Velocity limit hit! "
                f"Agent attempted {len(self.call_timestamps)} calls in 60s (Possible Infinite Loop)."
            )
            
        if self.total_spent + estimated_cost_usd > self.max_budget_usd:
            raise PermissionError(
                f"[CLAUDE_INC FIREWALL] BLOCKED: Budget limit reached! "
                f"Spent: ${self.total_spent:.4f} / Limit: ${self.max_budget_usd:.4f}"
            )
            
        self.call_timestamps.append(now)

    def log_spend(self, actual_cost_usd: float) -> None:
        """Records successful token spend and updates persistent ledger."""
        self.total_spent += actual_cost_usd
        self._save_ledger()

    def guard(self, estimated_cost: float = 0.01) -> Callable:
        """
        Zero-touch decorator for automatic function interception, 
        transient retry backoff, and financial safety guarding.
        """
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                retries = 0
                while retries <= self.max_retries:
                    try:
                        self.pre_flight_check(estimated_cost)
                        result = func(*args, **kwargs)
                        self.log_spend(estimated_cost)
                        return result
                    except (ConnectionError, TimeoutError, IOError) as transient_err:
                        retries += 1
                        if retries > self.max_retries:
                            raise RuntimeError(
                                f"[CLAUDE_INC RESILIENCE] Max retries ({self.max_retries}) "
                                f"exceeded for transient error: {transient_err}"
                            )
                        backoff = 0.5 * (2 ** retries)
                        print(f"[CLAUDE_INC WARNING] Transient glitch. Retrying in {backoff}s (Attempt {retries}/{self.max_retries})...")
                        time.sleep(backoff)
                    except (PermissionError, RuntimeError) as firewall_err:
                        # Hard financial or loop blocks propagate immediately without retry
                        raise firewall_err
            return wrapper
        return decorator

# Global default instance for easy zero-touch imports
default_firewall = AgentFirewall()