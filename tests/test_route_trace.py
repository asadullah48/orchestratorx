"""Route trace: one entry per supervisor decision, with the rule and its inputs.

Runs use the synthetic books from the CLI demo and the offline FakeLLM.
"""

from __future__ import annotations

import pytest

from orchestratorx import Orchestrator
from orchestratorx.cli import CLEAN_BOOK, CONCENTRATED_BOOK, RESTRICTED_BOOK
from orchestratorx.llm import FakeLLM
from orchestratorx.policy import (
    REPORTER,
    SUPERVISOR,
    CircuitBreaker,
    next_hop,
    traced_next_hop,
)
from orchestratorx.state import initial_state

OBJECTIVE = "Review the book against the mandate"


def breaker(agent: str) -> str:
    return f"breaker.is_open({agent})"


# What next_hop() reads on each hop, given its early returns (see policy._route).
EXPECTED = {
    "clean": (
        CLEAN_BOOK,
        [
            ("required_specialist", {"halted", "visited", breaker("RiskModeler")}),
            ("required_specialist", {"halted", "visited", breaker("ComplianceChecker")}),
            ("report", {"halted", "visited", "escalation"}),
        ],
    ),
    "concentrated": (
        CONCENTRATED_BOOK,
        [
            ("required_specialist", {"halted", "visited", breaker("RiskModeler")}),
            ("required_specialist", {"halted", "visited", breaker("ComplianceChecker")}),
            (
                "mitigate",
                {"halted", "visited", "escalation", "mitigation_rounds", breaker("ClientAdvisor")},
            ),
            ("report", {"halted", "visited", "escalation"}),
        ],
    ),
    "restricted": (
        RESTRICTED_BOOK,
        [
            ("required_specialist", {"halted", "visited", breaker("RiskModeler")}),
            ("required_specialist", {"halted", "visited", breaker("ComplianceChecker")}),
            ("halted", {"halted"}),
        ],
    ),
}

SCENARIOS = list(EXPECTED)


def traced_run(book: dict, *, trace_routes: bool = True):
    orchestrator = Orchestrator(llm=FakeLLM(), trace_routes=trace_routes)
    final, _ = orchestrator.run(OBJECTIVE, book)
    return final, orchestrator.route_traces.get(final["run_id"])


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_same_input_gives_the_same_trace(scenario):
    book = EXPECTED[scenario][0]
    _, first = traced_run(book)
    _, second = traced_run(book)
    assert first.meta.run_id != second.meta.run_id
    assert first.comparable() == second.comparable()


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_one_entry_per_hop_numbered_without_gaps(scenario):
    final, route = traced_run(EXPECTED[scenario][0])
    assert len(route.entries) == final["visited"].count(SUPERVISOR)
    assert [e.step for e in route.entries] == list(range(1, len(route.entries) + 1))


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_observed_holds_exactly_what_the_rule_read(scenario):
    book, expected = EXPECTED[scenario]
    final, route = traced_run(book)
    assert [(e.rule, set(e.observed)) for e in route.entries] == expected

    # The recorded values are the ones routing saw: ``visited`` at each
    # decision is the run's path up to that supervisor visit.
    supervisor_at = [i for i, node in enumerate(final["visited"]) if node == SUPERVISOR]
    for entry, index in zip(route.entries, supervisor_at, strict=True):
        if "visited" in entry.observed:
            assert entry.observed["visited"] == final["visited"][:index]


def test_open_breaker_is_recorded_as_an_input():
    state = initial_state(OBJECTIVE)
    tripped = CircuitBreaker(threshold=1)
    tripped.record_failure("RiskModeler")

    target, rule, observed = traced_next_hop(state, tripped)

    assert target == next_hop(state, tripped) == "ComplianceChecker"
    assert rule == "required_specialist"
    assert observed == {
        "halted": False,
        "visited": [],
        breaker("RiskModeler"): True,
        breaker("ComplianceChecker"): False,
    }


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_last_entry_ends_at_the_runs_final_node(scenario):
    final, route = traced_run(EXPECTED[scenario][0])
    assert route.entries[-1].to_node == final["visited"][-1] == REPORTER


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_mermaid_has_one_labelled_edge_per_entry(scenario):
    _, route = traced_run(EXPECTED[scenario][0])
    lines = route.to_mermaid().splitlines()
    assert lines[0] == "flowchart LR"
    edges = [line for line in lines if "-->" in line]
    assert len(edges) == len(route.entries)
    for edge, entry in zip(edges, route.entries, strict=True):
        assert f"|{entry.step}: {entry.rule}|" in edge


def _comparable_state(final: dict) -> dict:
    """The run result without values that differ between any two runs."""
    out = {k: v for k, v in final.items() if k != "run_id"}
    out["results"] = [{k: v for k, v in r.items() if k != "latency_ms"} for r in final["results"]]
    return out


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_tracing_off_gives_the_same_result_as_tracing_on(scenario):
    book = EXPECTED[scenario][0]
    final_off, route_off = traced_run(book, trace_routes=False)
    final_on, _ = traced_run(book, trace_routes=True)
    assert route_off is None
    assert _comparable_state(final_off) == _comparable_state(final_on)
