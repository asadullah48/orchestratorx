"""Retry, circuit breaking, and degradation under partial failure.

The property worth protecting: a failing specialist must not be able to hang
the run, silently vanish from the record, or fabricate a clean report.
"""

from __future__ import annotations

import pytest

from orchestratorx import Orchestrator
from orchestratorx.agents import BaseAgent, default_specialists
from orchestratorx.errors import (
    AgentFailureError,
    CircuitOpenError,
    PolicyViolationError,
)
from orchestratorx.llm import FakeLLM
from orchestratorx.policy import REPORTER, CircuitBreaker, RetryPolicy
from orchestratorx.state import OrchestrationState

CLEAN_BOOK = {
    "client": "ACME-PENSION-01",
    "leverage": 1.0,
    "positions": [
        {"symbol": "UTL", "issuer": "Issuer One", "sector": "utilities", "weight": 0.20},
        {"symbol": "HLT", "issuer": "Issuer Two", "sector": "healthcare", "weight": 0.20},
        {"symbol": "CNS", "issuer": "Issuer Three", "sector": "consumer", "weight": 0.20},
        {"symbol": "FIN", "issuer": "Issuer Four", "sector": "financials", "weight": 0.20},
        {"symbol": "ENR", "issuer": "Issuer Five", "sector": "energy", "weight": 0.20},
    ],
}


class FlakyAgent(BaseAgent):
    """Fails a set number of times, then succeeds."""

    name = "RiskModeler"

    def __init__(self, fail_times: int) -> None:
        super().__init__(FakeLLM())
        self.fail_times = fail_times
        self.calls = 0

    def work(self, state: OrchestrationState):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise AgentFailureError("simulated transport failure", agent=self.name)
        return {"recovered_after": self.calls}, []


# --------------------------------------------------------------------------
# RetryPolicy
# --------------------------------------------------------------------------


def test_success_on_first_attempt_does_not_retry():
    policy = RetryPolicy(max_attempts=3, backoff_seconds=0.0)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    result, attempts = policy.run(fn, agent="X")
    assert (result, attempts, calls["n"]) == ("ok", 1, 1)


def test_retries_until_success_and_reports_the_attempt_count():
    policy = RetryPolicy(max_attempts=3, backoff_seconds=0.0)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise AgentFailureError("boom")
        return "ok"

    result, attempts = policy.run(fn, agent="X")
    assert (result, attempts) == ("ok", 3)


def test_gives_up_after_max_attempts():
    policy = RetryPolicy(max_attempts=2, backoff_seconds=0.0)

    def fn():
        raise AgentFailureError("always")

    with pytest.raises(AgentFailureError) as excinfo:
        policy.run(fn, agent="RiskModeler")
    assert "after 2 attempts" in str(excinfo.value)


def test_policy_violations_are_never_retried():
    """Retrying a deterministic error just reproduces it."""
    policy = RetryPolicy(max_attempts=5, backoff_seconds=0.0)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise PolicyViolationError("illegal route")

    with pytest.raises(PolicyViolationError):
        policy.run(fn, agent="X")
    assert calls["n"] == 1, "a non-retryable error must not be attempted twice"


# --------------------------------------------------------------------------
# CircuitBreaker
# --------------------------------------------------------------------------


def test_breaker_opens_at_the_threshold():
    breaker = CircuitBreaker(threshold=2)
    assert not breaker.is_open("A")
    breaker.record_failure("A")
    assert not breaker.is_open("A")
    breaker.record_failure("A")
    assert breaker.is_open("A")


def test_guard_raises_once_open():
    breaker = CircuitBreaker(threshold=1)
    breaker.record_failure("A")
    with pytest.raises(CircuitOpenError):
        breaker.guard("A")


def test_success_resets_the_counter():
    breaker = CircuitBreaker(threshold=2)
    breaker.record_failure("A")
    breaker.record_success("A")
    breaker.record_failure("A")
    assert not breaker.is_open("A")


def test_breaker_is_per_agent():
    breaker = CircuitBreaker(threshold=1)
    breaker.record_failure("A")
    assert breaker.is_open("A")
    assert not breaker.is_open("B")


# --------------------------------------------------------------------------
# Degradation in a real run
# --------------------------------------------------------------------------


def test_transient_failure_is_retried_and_the_run_still_completes():
    specialists = default_specialists(FakeLLM())
    specialists["RiskModeler"] = FlakyAgent(fail_times=1)
    orchestrator = Orchestrator(
        specialists, llm=FakeLLM(), retry=RetryPolicy(max_attempts=3, backoff_seconds=0.0)
    )

    final, run_trace = orchestrator.run("Review", CLEAN_BOOK)

    assert run_trace.path[-1] == REPORTER
    assert final["errors"] == [], "a recovered failure is not a run error"
    risk = next(r for r in final["results"] if r["agent"] == "RiskModeler")
    assert risk["attempts"] == 2


def test_permanent_failure_is_recorded_and_the_run_still_terminates():
    """Degrade, don't hang: the report must still be produced and say so."""
    specialists = default_specialists(FakeLLM())
    specialists["RiskModeler"] = FlakyAgent(fail_times=99)
    orchestrator = Orchestrator(
        specialists,
        llm=FakeLLM(),
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0.0),
        breaker_threshold=1,
    )

    final, run_trace = orchestrator.run("Review", CLEAN_BOOK)

    assert run_trace.path[-1] == REPORTER, "the run must terminate"
    assert any(e["agent"] == "RiskModeler" for e in final["errors"])
    assert run_trace.failed_steps, "the failure must appear in the audit trail"
    # ComplianceChecker is independent and must still have run.
    assert "ComplianceChecker" in run_trace.path


def test_a_failing_specialist_cannot_exhaust_the_hop_budget():
    specialists = default_specialists(FakeLLM())
    specialists["RiskModeler"] = FlakyAgent(fail_times=99)
    orchestrator = Orchestrator(
        specialists,
        llm=FakeLLM(),
        retry=RetryPolicy(max_attempts=1, backoff_seconds=0.0),
        breaker_threshold=1,
    )

    _final, run_trace = orchestrator.run("Review", CLEAN_BOOK)
    assert run_trace.path.count("RiskModeler") == 1, "breaker must stop repeat attempts"
