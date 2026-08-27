"""End-to-end graph behaviour, the audit trace, and run isolation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from orchestratorx import Orchestrator, mermaid
from orchestratorx.llm import FakeLLM, get_client
from orchestratorx.policy import REPORTER, SUPERVISOR
from orchestratorx.trace import get as get_trace

# --------------------------------------------------------------------------
# Synthetic books
# --------------------------------------------------------------------------

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

CONCENTRATED_BOOK = {
    "client": "ACME-PENSION-02",
    "leverage": 1.0,
    "positions": [
        {"symbol": "AAA", "issuer": "Issuer Alpha", "sector": "technology", "weight": 0.60},
        {"symbol": "BBB", "issuer": "Issuer Beta", "sector": "utilities", "weight": 0.40},
    ],
}

RESTRICTED_BOOK = {
    "client": "ACME-PENSION-03",
    "leverage": 1.0,
    "positions": [
        {"symbol": "TOB", "issuer": "Issuer Six", "sector": "tobacco", "weight": 0.20},
        {"symbol": "UTL", "issuer": "Issuer Seven", "sector": "utilities", "weight": 0.20},
        {"symbol": "HLT", "issuer": "Issuer Eight", "sector": "healthcare", "weight": 0.20},
        {"symbol": "CNS", "issuer": "Issuer Nine", "sector": "consumer", "weight": 0.20},
        {"symbol": "FIN", "issuer": "Issuer Ten", "sector": "financials", "weight": 0.20},
    ],
}


@pytest.fixture
def orchestrator() -> Orchestrator:
    return Orchestrator(llm=FakeLLM())


# --------------------------------------------------------------------------
# Offline guarantee
# --------------------------------------------------------------------------


def test_default_reasoner_needs_no_key():
    """The whole suite depends on this being true."""
    assert get_client().name == "fake"


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_clean_book_produces_a_report_with_no_findings(orchestrator):
    final, run_trace = orchestrator.run("Review the book", CLEAN_BOOK)

    assert final["findings"] == []
    assert final["escalation"] == "continue"
    assert final["halted"] is False
    assert "ACME-PENSION-01" in final["report"]
    assert "Findings: none." in final["report"]
    # No escalation, so the advisor is never reached.
    assert "ClientAdvisor" not in run_trace.path


def test_every_run_visits_both_required_specialists(orchestrator):
    _final, run_trace = orchestrator.run("Review the book", CLEAN_BOOK)
    assert "RiskModeler" in run_trace.path
    assert "ComplianceChecker" in run_trace.path
    assert run_trace.path[0] == SUPERVISOR
    assert run_trace.path[-1] == REPORTER


# --------------------------------------------------------------------------
# Escalation paths
# --------------------------------------------------------------------------


def test_high_finding_triggers_one_mitigation_round_then_terminates(orchestrator):
    final, run_trace = orchestrator.run("Review the book", CONCENTRATED_BOOK)

    codes = {f["code"] for f in final["findings"]}
    assert "CMP-002" in codes
    assert run_trace.path.count("ClientAdvisor") == 1, "mitigation must be bounded at one round"
    assert final["mitigation_rounds"] == 1
    assert run_trace.path[-1] == REPORTER
    assert final["halted"] is False
    assert "Proposed mitigations:" in final["report"]


def test_critical_finding_halts_and_withholds_the_report(orchestrator):
    final, _run_trace = orchestrator.run("Review the book", RESTRICTED_BOOK)

    codes = {f["code"] for f in final["findings"]}
    assert "CMP-003" in codes
    assert final["halted"] is True
    assert final["escalation"] == "halt"
    assert "RUN HALTED" in final["report"]


def test_halt_skips_the_advisor(orchestrator):
    """A halted run must not spend hops proposing mitigations."""
    _final, run_trace = orchestrator.run("Review the book", RESTRICTED_BOOK)
    assert "ClientAdvisor" not in run_trace.path


# --------------------------------------------------------------------------
# Trace
# --------------------------------------------------------------------------


def test_trace_records_one_event_per_hop_in_order(orchestrator):
    final, run_trace = orchestrator.run("Review the book", CLEAN_BOOK)

    assert [e.step for e in run_trace.events] == list(range(len(run_trace.events)))
    assert run_trace.path == final["visited"]
    assert run_trace.failed_steps == []


def test_trace_is_retrievable_by_run_id_after_the_run(orchestrator):
    final, run_trace = orchestrator.run("Review the book", CLEAN_BOOK)
    fetched = get_trace(final["run_id"])
    assert fetched is not None
    assert fetched.run_id == run_trace.run_id
    assert fetched.to_dict()["steps"] == len(run_trace.events)


def test_trace_serializes_to_json(orchestrator):
    _final, run_trace = orchestrator.run("Review the book", CLEAN_BOOK)
    payload = run_trace.to_dict()
    assert payload["run_id"].startswith("run_")
    assert payload["path"] == run_trace.path
    assert all("at" in e for e in payload["events"])


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


def test_concurrent_runs_do_not_share_state(orchestrator):
    """One compiled graph, several runs: findings must not bleed across."""
    books = [CLEAN_BOOK, CONCENTRATED_BOOK, CLEAN_BOOK, RESTRICTED_BOOK]
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda b: orchestrator.run("Review", b), books))

    run_ids = [final["run_id"] for final, _ in outcomes]
    assert len(set(run_ids)) == len(books), "each run needs its own id"

    clean_a, clean_b = outcomes[0][0], outcomes[2][0]
    assert clean_a["findings"] == []
    assert clean_b["findings"] == []
    assert outcomes[3][0]["halted"] is True


def test_separate_runs_get_separate_traces(orchestrator):
    first, trace_a = orchestrator.run("Review", CLEAN_BOOK)
    second, trace_b = orchestrator.run("Review", CLEAN_BOOK)
    assert first["run_id"] != second["run_id"]
    assert trace_a.run_id != trace_b.run_id


# --------------------------------------------------------------------------
# Docs
# --------------------------------------------------------------------------


def test_mermaid_describes_the_real_graph():
    diagram = mermaid()
    for node in ("supervisor", "RiskModeler", "ComplianceChecker", "ClientAdvisor", "Reporter"):
        assert node in diagram
