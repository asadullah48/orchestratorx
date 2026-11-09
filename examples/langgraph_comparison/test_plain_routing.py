"""Run with: python -m pytest examples/langgraph_comparison (in this example's venv)."""

from plain_routing import run


def test_reaches_the_same_path_as_the_sample_trace():
    assert run()["visited"] == ["RiskModeler", "ComplianceChecker", "ClientAdvisor", "Reporter"]


def test_same_input_gives_the_same_path():
    assert run()["visited"] == run()["visited"]
