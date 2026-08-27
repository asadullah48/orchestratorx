"""Failure taxonomy.

The distinction that matters operationally is ``retryable``: a transport blip
should be retried, a policy violation never should. Collapsing both into one
generic exception is what turns a circuit breaker into an infinite loop.
"""

from __future__ import annotations

from typing import Any


class OrchestratorError(Exception):
    """Base class. Carries enough context to land in ``state['errors']``."""

    retryable: bool = False

    def __init__(self, message: str, *, agent: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.agent = agent

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": type(self).__name__,
            "agent": self.agent,
            "message": self.message,
            "retryable": self.retryable,
        }


class AgentFailureError(OrchestratorError):
    """A specialist raised while doing its work. Usually worth another attempt."""

    retryable = True


class CircuitOpenError(OrchestratorError):
    """The breaker for this specialist is open; stop calling it."""

    retryable = False


class PolicyViolationError(OrchestratorError):
    """The supervisor was asked for an illegal transition.

    Never retryable: retrying an illegal route just reproduces the same illegal
    route, which is how a "resilient" orchestrator spins forever.
    """

    retryable = False


class BudgetExceededError(OrchestratorError):
    """The run hit its hop budget before reaching a terminal state."""

    retryable = False
