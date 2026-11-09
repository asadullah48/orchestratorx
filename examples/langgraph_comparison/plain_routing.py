"""The same routing rules as OrchestratorX, written as plain LangGraph.

There is no supervisor node here. The routing function is attached as a
conditional edge to START and to each specialist, and Reporter goes straight
to END. The routing rules live inside that edge's function. This is the
common way to route in LangGraph, and it is what docs/typed-state-routing.md
compares against.

The specialists are stubs that return fixed, synthetic findings. They copy
the shape of the "Concentrated and over-levered" demo run (one medium and one
high finding), so the route should match docs/samples/route-trace-sample.mmd:

    START -> RiskModeler -> ComplianceChecker -> ClientAdvisor -> Reporter

No API keys, no network, and nothing from src/orchestratorx is imported.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

REQUIRED = ("RiskModeler", "ComplianceChecker")
MAX_MITIGATION_ROUNDS = 1
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class State(TypedDict):
    visited: Annotated[list[str], operator.add]
    findings: Annotated[list[dict[str, Any]], operator.add]
    mitigation_rounds: int
    report: str


# -- stub specialists (synthetic, fixed output) ------------------------------


def risk_modeler(state: State) -> dict[str, Any]:
    return {"visited": ["RiskModeler"], "findings": [{"code": "RSK", "severity": "medium"}]}


def compliance_checker(state: State) -> dict[str, Any]:
    return {"visited": ["ComplianceChecker"], "findings": [{"code": "CMP", "severity": "high"}]}


def client_advisor(state: State) -> dict[str, Any]:
    return {"visited": ["ClientAdvisor"], "mitigation_rounds": state["mitigation_rounds"] + 1}


def reporter(state: State) -> dict[str, Any]:
    return {"visited": ["Reporter"], "report": f"{len(state['findings'])} findings"}


# -- routing: the rules sit inside the conditional-edge function -------------


def route(state: State) -> str:
    worst = max((SEVERITY_RANK[f["severity"]] for f in state["findings"]), default=-1)
    if worst == SEVERITY_RANK["critical"]:
        return "Reporter"  # halt
    for agent in REQUIRED:
        if agent not in state["visited"]:
            return agent
    if worst >= SEVERITY_RANK["high"] and state["mitigation_rounds"] < MAX_MITIGATION_ROUNDS:
        return "ClientAdvisor"
    return "Reporter"


def build():
    graph = StateGraph(State)
    graph.add_node("RiskModeler", risk_modeler)
    graph.add_node("ComplianceChecker", compliance_checker)
    graph.add_node("ClientAdvisor", client_advisor)
    graph.add_node("Reporter", reporter)

    targets = {
        name: name for name in ("RiskModeler", "ComplianceChecker", "ClientAdvisor", "Reporter")
    }
    graph.add_conditional_edges(START, route, targets)
    for name in ("RiskModeler", "ComplianceChecker", "ClientAdvisor"):
        graph.add_conditional_edges(name, route, targets)
    graph.add_edge("Reporter", END)
    return graph.compile()


def run() -> State:
    return build().invoke({"visited": [], "findings": [], "mitigation_rounds": 0, "report": ""})


if __name__ == "__main__":
    final = run()
    print("path    :", " -> ".join(["START", *final["visited"]]))
    print("findings:", [f"{f['code']}({f['severity']})" for f in final["findings"]])
    print("report  :", final["report"])
