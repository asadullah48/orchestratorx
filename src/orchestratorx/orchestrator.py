"""Supervisor node and graph assembly.

Graph shape::

    START -> supervisor -> { RiskModeler, ComplianceChecker, ClientAdvisor }
                 ^________________________|
             -> Reporter -> END

Two properties are structural rather than conventional:

* **Only the supervisor routes.** Specialists return to the supervisor and
  never to each other, so every routing decision passes through one function
  that can be tested in isolation.
* **Runs are isolated by ``run_id``.** The trace and the circuit breaker are
  looked up per run rather than held on the instance, so concurrent runs on one
  compiled graph cannot leak failure counts into each other.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from langgraph.graph import END, START, StateGraph

from orchestratorx import trace as trace_mod
from orchestratorx.agents import BaseAgent, default_specialists
from orchestratorx.errors import CircuitOpenError, OrchestratorError
from orchestratorx.llm import LLMClient, get_client
from orchestratorx.policy import (
    REPORTER,
    RULE_SUPERVISOR_ERROR,
    SPECIALISTS,
    SUPERVISOR,
    CircuitBreaker,
    RetryPolicy,
    check_budget,
    decide_escalation,
    next_hop,
    traced_next_hop,
    validate_transition,
)
from orchestratorx.route_trace import RouteTrace, RouteTraceMeta
from orchestratorx.state import Escalation, OrchestrationState, initial_state
from orchestratorx.trace import RunTrace, _now_iso


class Orchestrator:
    """Builds and runs the supervisor graph."""

    def __init__(
        self,
        specialists: dict[str, BaseAgent] | None = None,
        *,
        llm: LLMClient | None = None,
        retry: RetryPolicy | None = None,
        breaker_threshold: int = 2,
        trace_routes: bool = False,
    ) -> None:
        self.llm = llm or get_client()
        self.specialists = specialists or default_specialists(self.llm)
        self.retry = retry or RetryPolicy()
        self.breaker_threshold = breaker_threshold
        self._breakers: dict[str, CircuitBreaker] = {}
        self.trace_routes = trace_routes
        self.route_traces: dict[str, RouteTrace] = {}
        self.app = self._build()

    # -- per-run collaborators -------------------------------------------

    def breaker_for(self, run_id: str) -> CircuitBreaker:
        return self._breakers.setdefault(run_id, CircuitBreaker(self.breaker_threshold))

    @staticmethod
    def trace_for(run_id: str) -> RunTrace:
        existing = trace_mod.get(run_id)
        if existing is None:
            existing = trace_mod.register(RunTrace(run_id=run_id))
        return existing

    def route_trace_for(self, run_id: str) -> RouteTrace:
        return self.route_traces.setdefault(run_id, RouteTrace(meta=RouteTraceMeta(run_id=run_id)))

    # -- nodes ------------------------------------------------------------

    def _supervisor(self, state: OrchestrationState) -> dict[str, Any]:
        started = perf_counter()
        run_id = state["run_id"]
        run_trace = self.trace_for(run_id)
        breaker = self.breaker_for(run_id)

        findings = state.get("findings", [])
        rounds = state.get("mitigation_rounds", 0)
        visited = state.get("visited", [])
        came_from = visited[-1] if visited else "START"

        try:
            check_budget(state)
            escalation = decide_escalation(findings, rounds)
            halted = escalation is Escalation.HALT
            working = dict(state)
            working["escalation"] = escalation.value
            working["halted"] = halted
            if self.trace_routes:
                target, rule, observed = traced_next_hop(working, breaker)  # type: ignore[arg-type]
            else:
                target = next_hop(working, breaker)  # type: ignore[arg-type]
            validate_transition(SUPERVISOR, target)
        except OrchestratorError as exc:
            # Budget exhaustion or an illegal route ends the run at the
            # reporter, which explains what happened rather than going silent.
            if self.trace_routes:
                # The routing rules did not pick this hop, so nothing was observed.
                self.route_trace_for(run_id).record(came_from, REPORTER, RULE_SUPERVISOR_ERROR, {})
            run_trace.record(
                SUPERVISOR,
                duration_ms=(perf_counter() - started) * 1000,
                error=exc.message,
            )
            return {
                "visited": [SUPERVISOR],
                "errors": [exc.to_dict()],
                "next_agent": REPORTER,
                "halted": True,
                "escalation": Escalation.HALT.value,
            }

        if self.trace_routes:
            self.route_trace_for(run_id).record(came_from, target, rule, observed)
        run_trace.record(
            SUPERVISOR,
            escalation=escalation.value,
            duration_ms=(perf_counter() - started) * 1000,
            detail={"target": target, "breaker": breaker.snapshot()},
        )
        return {
            "visited": [SUPERVISOR],
            "next_agent": target,
            "escalation": escalation.value,
            "halted": halted,
        }

    def _specialist_node(self, name: str):
        agent = self.specialists[name]

        def node(state: OrchestrationState) -> dict[str, Any]:
            started = perf_counter()
            run_id = state["run_id"]
            run_trace = self.trace_for(run_id)
            breaker = self.breaker_for(run_id)

            try:
                breaker.guard(name)
                result, attempts = self.retry.run(lambda: agent.run(state), agent=name)
            except CircuitOpenError as exc:
                run_trace.record(
                    name, duration_ms=(perf_counter() - started) * 1000, error=exc.message
                )
                return {"visited": [name], "errors": [exc.to_dict()]}
            except OrchestratorError as exc:
                breaker.record_failure(name)
                run_trace.record(
                    name, duration_ms=(perf_counter() - started) * 1000, error=exc.message
                )
                return {"visited": [name], "errors": [exc.to_dict()]}

            breaker.record_success(name)
            result.attempts = attempts
            findings = [f.to_dict() for f in result.findings]
            run_trace.record(
                name,
                findings_added=len(findings),
                duration_ms=(perf_counter() - started) * 1000,
                detail={"attempts": attempts},
            )

            update: dict[str, Any] = {
                "visited": [name],
                "results": [result.to_dict()],
                "findings": findings,
            }
            if name == "ClientAdvisor":
                # Scalar field: read-modify-write, since there is no reducer.
                update["mitigation_rounds"] = state.get("mitigation_rounds", 0) + 1
            return update

        node.__name__ = f"node_{name}"
        return node

    def _reporter_node(self, state: OrchestrationState) -> dict[str, Any]:
        started = perf_counter()
        run_trace = self.trace_for(state["run_id"])
        agent = self.specialists[REPORTER]

        try:
            result = agent.run(state)
        except OrchestratorError as exc:
            run_trace.record(
                REPORTER, duration_ms=(perf_counter() - started) * 1000, error=exc.message
            )
            return {"visited": [REPORTER], "errors": [exc.to_dict()], "report": ""}

        run_trace.record(REPORTER, duration_ms=(perf_counter() - started) * 1000)
        return {
            "visited": [REPORTER],
            "results": [result.to_dict()],
            "report": result.payload.get("report", ""),
        }

    # -- assembly ---------------------------------------------------------

    def _build(self) -> Any:
        graph = StateGraph(OrchestrationState)
        graph.add_node(SUPERVISOR, self._supervisor)
        for name in SPECIALISTS:
            graph.add_node(name, self._specialist_node(name))
        graph.add_node(REPORTER, self._reporter_node)

        graph.add_edge(START, SUPERVISOR)
        graph.add_conditional_edges(
            SUPERVISOR,
            lambda state: state["next_agent"],
            {name: name for name in (*SPECIALISTS, REPORTER)},
        )
        for name in SPECIALISTS:
            graph.add_edge(name, SUPERVISOR)
        graph.add_edge(REPORTER, END)
        return graph.compile()

    # -- execution --------------------------------------------------------

    def run(
        self,
        objective: str,
        request: dict[str, Any] | None = None,
    ) -> tuple[OrchestrationState, RunTrace]:
        """Run one orchestration to completion.

        Returns the final state and its trace. The trace stays registered so
        the API can serve it later by ``run_id``.
        """
        state = initial_state(objective, request)
        run_trace = trace_mod.register(RunTrace(run_id=state["run_id"], objective=objective))
        final: OrchestrationState = self.app.invoke(state)
        if self.trace_routes:
            self.route_trace_for(state["run_id"]).meta.finished_at = _now_iso()
        return final, run_trace


def run_once(
    objective: str,
    request: dict[str, Any] | None = None,
    **kw: Any,
) -> tuple[OrchestrationState, RunTrace]:
    """Convenience wrapper for a single run with default collaborators."""
    return Orchestrator(**kw).run(objective, request)


MERMAID = """flowchart TD
    START([run]) --> S[supervisor]
    S -->|next_agent| RM[RiskModeler]
    S -->|next_agent| CC[ComplianceChecker]
    S -->|escalation = mitigate| CA[ClientAdvisor]
    S -->|required done| RP[Reporter]
    RM --> S
    CC --> S
    CA --> S
    RP --> DONE([report])
"""


def mermaid() -> str:
    """Graph shape as mermaid, for docs and the CLI."""
    return MERMAID
