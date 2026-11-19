"""agentfuse: pre-call spend hard-stops and a deterministic numeric firewall for LLM agents."""
from .audit import AuditLog, verify_chain
from .errors import (BudgetExceeded, CircuitOpen, FuseError, LoopDetected, UngroundedNumber,
                     UnknownModel)
from .guard import Guard, GuardedResult, Policy
from .ledger import Ledger, Limit, to_micro
from .loop import LoopDetector
from .pricing import Price, PriceBook
from .truth import Evidence, NumericFirewall

__version__ = "0.2.0"
