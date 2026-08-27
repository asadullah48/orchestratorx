"""Routing policy, reliability primitives, and the escalation rule.

Three separable concerns live here on purpose:

* **Routing** — which node may follow which, enforced rather than assumed.
* **Reliability** — retry with backoff, a per-specialist circuit breaker, and a
  hop budget for the whole run.
* **Escalation** — what to do when a specialist reports a finding. That is a
  business judgement, not a technical fact, so it is isolated in one function
  (see ``=== ESCALATION SEAM ===`` below).

A supervisor that loops needs a hop budget, not just a retry cap. Retries bound
one node's failures; only a budget bounds the *cycle*. "Route back for
mitigation, then re-check" is otherwise a legal infinite loop that no single
component is misbehaving to cause.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from orchestratorx.errors import (
    AgentFailureError,
    BudgetExceededError,
    CircuitOpenError,
    PolicyViolationError,
)
from orchestratorx.state import Escalation, OrchestrationState, Severity, max_severity

SUPERVISOR = "supervisor"
REPORTER = "Reporter"

REQUIRED_SPECIALISTS: tuple[str, ...] = ("RiskModeler", "ComplianceChecker")
"""Must both run before a report may be produced."""

OPTIONAL_SPECIALISTS: tuple[str, ...] = ("ClientAdvisor",)
"""Reached only when the escalation rule asks for a mitigation proposal."""

SPECIALISTS: tuple[str, ...] = REQUIRED_SPECIALISTS + OPTIONAL_SPECIALISTS

LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    SUPERVISOR: frozenset({*SPECIALISTS, REPORTER}),
    **{name: frozenset({SUPERVISOR}) for name in SPECIALISTS},
    REPORTER: frozenset(),
}
"""Adjacency the supervisor is allowed to produce.

Specialists may only return to the supervisor — they never hand off to each
other. That is what keeps the routing decision in exactly one place.
"""

MAX_HOPS = 12
"""Hard ceiling on node executions per run."""

MAX_MITIGATION_ROUNDS = 1
"""Bounding this is what makes termination provable rather than likely."""


def validate_transition(source: str, target: str) -> None:
    """Raise if ``source -> target`` is not an edge the graph permits."""
    allowed = LEGAL_TRANSITIONS.get(source)
    if allowed is None:
        raise PolicyViolationError(f"unknown node: {source!r}")
    if target not in allowed:
        raise PolicyViolationError(
            f"illegal transition {source!r} -> {target!r}; allowed: {sorted(allowed)}",
            agent=source,
        )


def check_budget(state: OrchestrationState) -> None:
    """Raise once a run has taken more hops than the budget allows."""
    if len(state.get("visited", [])) >= MAX_HOPS:
        raise BudgetExceededError(f"run exceeded {MAX_HOPS} hops without terminating")


# --------------------------------------------------------------------------
# Reliability
# --------------------------------------------------------------------------

T = TypeVar("T")


@dataclass
class RetryPolicy:
    """Bounded retry with linear backoff.

    Only :class:`AgentFailureError` is retried. A ``PolicyViolationError`` is
    deterministic — retrying it reproduces it — so it propagates immediately.
    """

    max_attempts: int = 3
    backoff_seconds: float = 0.0

    def run(self, fn: Callable[[], T], *, agent: str = "") -> tuple[T, int]:
        """Call ``fn`` until it succeeds. Returns ``(result, attempts_used)``."""
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return fn(), attempt
            except AgentFailureError as exc:
                last = exc
                if attempt < self.max_attempts and self.backoff_seconds:
                    time.sleep(self.backoff_seconds * attempt)
        raise AgentFailureError(
            f"{agent or 'agent'} failed after {self.max_attempts} attempts: {last}",
            agent=agent,
        )


@dataclass
class CircuitBreaker:
    """Per-specialist breaker.

    Once a specialist has failed ``threshold`` times, calling it again within
    the same run is pointless, so the supervisor routes around it and records
    the reason instead of burning the hop budget.
    """

    threshold: int = 2
    failures: dict[str, int] = field(default_factory=dict)

    def record_failure(self, agent: str) -> None:
        self.failures[agent] = self.failures.get(agent, 0) + 1

    def record_success(self, agent: str) -> None:
        self.failures.pop(agent, None)

    def is_open(self, agent: str) -> bool:
        return self.failures.get(agent, 0) >= self.threshold

    def guard(self, agent: str) -> None:
        if self.is_open(agent):
            raise CircuitOpenError(
                f"circuit open for {agent} after {self.failures[agent]} failures",
                agent=agent,
            )

    def snapshot(self) -> dict[str, Any]:
        return {"threshold": self.threshold, "failures": dict(self.failures)}


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def next_hop(state: OrchestrationState, breaker: CircuitBreaker | None = None) -> str:
    """Pick the next node from typed state alone.

    Deterministic and model-free by design: the routing invariant is only
    testable if nothing here consults an LLM.
    """
    if state.get("halted"):
        return REPORTER

    visited = state.get("visited", [])

    # Required specialists first, skipping any whose breaker has opened.
    for agent in REQUIRED_SPECIALISTS:
        if agent in visited:
            continue
        if breaker is not None and breaker.is_open(agent):
            continue
        return agent

    if (
        state.get("escalation") == Escalation.MITIGATE.value
        and state.get("mitigation_rounds", 0) < MAX_MITIGATION_ROUNDS
        and not (breaker is not None and breaker.is_open("ClientAdvisor"))
    ):
        return "ClientAdvisor"

    return REPORTER


# --------------------------------------------------------------------------
# === ESCALATION SEAM ======================================================
# Specialists report *facts*. What the supervisor does about them is a risk
# posture, and organisations differ. It is isolated here so it can be replaced
# without touching routing, reliability, or any agent.
#
# Flagged in docs/NEXT-SESSION.md as needing the owner's judgement. The default
# below is: halt on critical, one bounded mitigation round on high, annotate
# and continue otherwise.
# --------------------------------------------------------------------------


def decide_escalation(
    findings: list[dict[str, Any]],
    mitigation_rounds: int = 0,
) -> Escalation:
    """Turn accumulated findings into the supervisor's next posture.

    The default is graduated rather than binary:

    * ``CRITICAL`` -> :attr:`Escalation.HALT`. A report built on a critical
      compliance failure is worse than no report, because it looks authoritative.
    * ``HIGH`` -> :attr:`Escalation.MITIGATE`, once. Route to ClientAdvisor for
      a mitigation proposal and re-check, bounded by
      :data:`MAX_MITIGATION_ROUNDS` so the cycle provably terminates.
    * anything lower -> :attr:`Escalation.CONTINUE`. Annotate and report.

    Trade-offs an operator may want to invert: a regulated desk might halt on
    ``HIGH`` too, accepting the abandoned work; a research desk might allow
    several mitigation rounds, accepting slower runs for a better proposal.
    """
    worst = max_severity(findings)
    if worst is None:
        return Escalation.CONTINUE
    if worst is Severity.CRITICAL:
        return Escalation.HALT
    if worst.rank >= Severity.HIGH.rank and mitigation_rounds < MAX_MITIGATION_ROUNDS:
        return Escalation.MITIGATE
    return Escalation.CONTINUE
