"""Routing invariants and the escalation seam.

These tests only exist because routing is model-free. If the supervisor
consulted an LLM, none of the assertions below could be made without a network
call and a pinned model version.
"""

from __future__ import annotations

import pytest

from orchestratorx.errors import BudgetExceededError, PolicyViolationError
from orchestratorx.policy import (
    LEGAL_TRANSITIONS,
    MAX_HOPS,
    MAX_MITIGATION_ROUNDS,
    REPORTER,
    REQUIRED_SPECIALISTS,
    SUPERVISOR,
    CircuitBreaker,
    check_budget,
    decide_escalation,
    next_hop,
    validate_transition,
)
from orchestratorx.state import Escalation, Severity, initial_state


def finding(code: str, severity: Severity) -> dict:
    return {
        "code": code,
        "severity": severity.value,
        "message": "m",
        "remediation": "r",
        "source": "ComplianceChecker",
    }


# --------------------------------------------------------------------------
# Legal transitions
# --------------------------------------------------------------------------


def test_supervisor_may_reach_every_specialist_and_the_reporter():
    allowed = LEGAL_TRANSITIONS[SUPERVISOR]
    assert {"RiskModeler", "ComplianceChecker", "ClientAdvisor", REPORTER} == set(allowed)


@pytest.mark.parametrize("specialist", ["RiskModeler", "ComplianceChecker", "ClientAdvisor"])
def test_specialists_may_only_return_to_the_supervisor(specialist):
    """No specialist hands off to another — routing stays in one place."""
    assert LEGAL_TRANSITIONS[specialist] == frozenset({SUPERVISOR})
    validate_transition(specialist, SUPERVISOR)
    with pytest.raises(PolicyViolationError):
        validate_transition(specialist, REPORTER)


def test_reporter_is_terminal():
    assert LEGAL_TRANSITIONS[REPORTER] == frozenset()


def test_unknown_node_is_a_policy_violation():
    with pytest.raises(PolicyViolationError):
        validate_transition("NotARealNode", SUPERVISOR)


# --------------------------------------------------------------------------
# Hop ordering
# --------------------------------------------------------------------------


def test_required_specialists_run_before_the_reporter():
    state = initial_state("obj", {})
    assert next_hop(state) == REQUIRED_SPECIALISTS[0]

    state["visited"] = [REQUIRED_SPECIALISTS[0]]
    assert next_hop(state) == REQUIRED_SPECIALISTS[1]

    state["visited"] = list(REQUIRED_SPECIALISTS)
    assert next_hop(state) == REPORTER


def test_compliance_checker_is_never_skipped_on_the_happy_path():
    """The invariant the whole design exists to guarantee."""
    state = initial_state("obj", {})
    seen: list[str] = []
    for _ in range(MAX_HOPS):
        hop = next_hop(state)
        seen.append(hop)
        if hop == REPORTER:
            break
        state["visited"] = state["visited"] + [hop]
    assert "ComplianceChecker" in seen
    assert seen.index("ComplianceChecker") < seen.index(REPORTER)


def test_halted_state_routes_straight_to_the_reporter():
    state = initial_state("obj", {})
    state["halted"] = True
    assert next_hop(state) == REPORTER


def test_mitigation_is_bounded_so_the_cycle_terminates():
    state = initial_state("obj", {})
    state["visited"] = list(REQUIRED_SPECIALISTS)
    state["escalation"] = Escalation.MITIGATE.value

    assert next_hop(state) == "ClientAdvisor"

    state["mitigation_rounds"] = MAX_MITIGATION_ROUNDS
    assert next_hop(state) == REPORTER


def test_open_breaker_routes_around_a_specialist():
    state = initial_state("obj", {})
    breaker = CircuitBreaker(threshold=1)
    breaker.record_failure("RiskModeler")
    assert next_hop(state, breaker) == "ComplianceChecker"


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------


def test_budget_allows_a_normal_run():
    state = initial_state("obj", {})
    state["visited"] = ["supervisor"] * 3
    check_budget(state)


def test_budget_stops_a_runaway_cycle():
    state = initial_state("obj", {})
    state["visited"] = ["supervisor"] * MAX_HOPS
    with pytest.raises(BudgetExceededError):
        check_budget(state)


# --------------------------------------------------------------------------
# Escalation seam
# --------------------------------------------------------------------------


def test_no_findings_continues():
    assert decide_escalation([]) is Escalation.CONTINUE


def test_low_severity_is_annotated_not_escalated():
    assert decide_escalation([finding("RSK-001", Severity.LOW)]) is Escalation.CONTINUE
    assert decide_escalation([finding("RSK-001", Severity.MEDIUM)]) is Escalation.CONTINUE


def test_high_severity_asks_for_one_mitigation_round():
    findings = [finding("CMP-002", Severity.HIGH)]
    assert decide_escalation(findings, 0) is Escalation.MITIGATE
    # Already mitigated once: annotate and move on rather than loop.
    assert decide_escalation(findings, MAX_MITIGATION_ROUNDS) is Escalation.CONTINUE


def test_critical_halts_regardless_of_mitigation_history():
    findings = [finding("CMP-003", Severity.CRITICAL)]
    assert decide_escalation(findings, 0) is Escalation.HALT
    assert decide_escalation(findings, 99) is Escalation.HALT


def test_worst_finding_drives_the_decision():
    findings = [
        finding("RSK-001", Severity.LOW),
        finding("CMP-003", Severity.CRITICAL),
        finding("CMP-002", Severity.HIGH),
    ]
    assert decide_escalation(findings) is Escalation.HALT
