"""Price book. Unknown model = fail closed, because an unbounded cost cannot be reserved."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .errors import UnknownModel


@dataclass(frozen=True)
class Price:
    in_per_mtok: float
    out_per_mtok: float
    search_usd: float = 0.0  # per web-search request

    def cost_usd(self, in_tokens: int, out_tokens: int, searches: int = 0) -> float:
        return (in_tokens * self.in_per_mtok + out_tokens * self.out_per_mtok) / 1e6 + searches * self.search_usd


# Anthropic list prices as checked in the owner's project on 14 Nov 2026. PRICES CHANGE:
# re-check on the provider's pricing page and override with PriceBook.register().
PRICES_VERIFIED_ON = "2026-11-14"
DEFAULT_PRICES: Dict[str, Price] = {
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.01),
    "claude-opus-5-5": Price(4.00, 20.00, 0.01),
}


class PriceBook:
    def __init__(self, prices: Optional[Dict[str, Price]] = None, fail_closed: bool = True):
        self.prices = dict(DEFAULT_PRICES if prices is None else prices)
        self.fail_closed = fail_closed

    def register(self, model: str, price: Price) -> None:
        self.prices[model] = price

    def get(self, model: str) -> Price:
        if model in self.prices:
            return self.prices[model]
        for key in sorted(self.prices, key=len, reverse=True):  # longest prefix wins
            if model.startswith(key):
                return self.prices[key]
        if self.fail_closed or not self.prices:
            raise UnknownModel(f"no price registered for model '{model}'; register one or set fail_closed=False")
        # Not failing closed: assume the most expensive known model (conservative).
        return max(self.prices.values(), key=lambda p: p.out_per_mtok)
