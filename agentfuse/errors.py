"""Every way agentfuse can stop a call. All inherit FuseError so one `except FuseError` catches them."""


class FuseError(Exception):
    """Base class for all agentfuse stops."""


class BudgetExceeded(FuseError):
    """Raised BEFORE the provider call when the worst-case cost would break a limit."""

    def __init__(self, limit, spent_micro, requested_micro):
        self.limit = limit
        self.spent_micro = spent_micro
        self.requested_micro = requested_micro
        super().__init__(
            f"blocked before the call: {limit.scope}/{limit.per} limit ${limit.usd:.4f} "
            f"(spent+reserved ${spent_micro / 1e6:.4f}, this call could cost up to ${requested_micro / 1e6:.4f})"
        )


class LoopDetected(FuseError):
    """The same request keeps repeating, or a run exceeded its step/time ceiling."""


class CircuitOpen(FuseError):
    """A breaker is open for this run, user, or globally. Calls are refused until reset or cooldown."""


class UnknownModel(FuseError):
    """No price known for this model, so the worst-case cost cannot be bounded (fail closed)."""


class UngroundedNumber(FuseError):
    """The output contained numbers that appear nowhere in the evidence (action='block')."""

    def __init__(self, report):
        self.report = report
        bad = ", ".join(f.text for f in report.ungrounded())
        super().__init__(f"output blocked: ungrounded numbers: {bad}")
