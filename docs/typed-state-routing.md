# Typed-state routing

This note compares two ways to write the same routing rules in LangGraph:

- **OrchestratorX** (`src/orchestratorx/`): one supervisor node picks every hop from typed
  state, and the supervisor checks each choice before the graph follows it.
- **A plain LangGraph version** (`examples/langgraph_comparison/plain_routing.py`): the same
  rules sit in a conditional-edge function attached to every node, with nothing around them.

Both run offline and both reach the same path for the "Concentrated and over-levered"
scenario. The point is not that one is faster; it is what each version can check and record.

## Routing as prompt text vs routing as typed state

In a prompt-routed system, a model reads instructions like "call the compliance checker
before writing the report" and decides the next agent itself. The route is then a model
output: to test it you need a live model, and the same input can route differently on
another run.

Routing as typed state means the next hop is a plain function of named, typed fields.
In OrchestratorX those fields are declared in `OrchestrationState`
(`src/orchestratorx/state.py`): `visited`, `findings`, `escalation`, `mitigation_rounds`,
`halted`, and others. The routing function reads only those fields and never calls a model.
The docstring of `next_hop` in `src/orchestratorx/policy.py` says this directly:
"Deterministic and model-free by design".

This repo contains **no prompt-routed implementation**. The prompt-text side above is the
problem the design avoids, not something measured here. Both versions compared below route
from typed state; they differ in what surrounds the routing function.

## The plain LangGraph version

`examples/langgraph_comparison/plain_routing.py` imports nothing from `src/orchestratorx/`.
It has four nodes with stub specialists that return fixed, synthetic findings (one medium,
one high), copying the shape of demo scenario 2. There is no supervisor node: `route()` is
attached as a conditional edge to `START` and to each specialist (`build()` in the same file).

Run from `examples/langgraph_comparison/`, with `langgraph==1.2.11` (pinned in that folder's
`requirements.txt`):

```
$ python plain_routing.py
path    : START -> RiskModeler -> ComplianceChecker -> ClientAdvisor -> Reporter
findings: ['RSK(medium)', 'CMP(high)']
report  : 2 findings
```

That is the same route as `docs/samples/route-trace-sample.mmd`, the recorded OrchestratorX
route trace for scenario 2:

```
START -->|1: required_specialist| RiskModeler
RiskModeler -->|2: required_specialist| ComplianceChecker
ComplianceChecker -->|3: mitigate| ClientAdvisor
ClientAdvisor -->|4: report| Reporter
```

`examples/langgraph_comparison/test_plain_routing.py` asserts that path and that two runs
give the same path.

## The rules, side by side

OrchestratorX, `_route()` in `src/orchestratorx/policy.py` (called by `next_hop` and
`traced_next_hop`):

```python
def _route(state: OrchestrationState, breaker: CircuitBreaker | None) -> tuple[str, str]:
    """The routing rules. Returns ``(target, rule_id)``."""
    if state.get("halted"):
        return REPORTER, RULE_HALTED

    visited = state.get("visited", [])

    # Required specialists first, skipping any whose breaker has opened.
    for agent in REQUIRED_SPECIALISTS:
        if agent in visited:
            continue
        if breaker is not None and breaker.is_open(agent):
            continue
        return agent, RULE_REQUIRED_SPECIALIST

    if (
        state.get("escalation") == Escalation.MITIGATE.value
        and state.get("mitigation_rounds", 0) < MAX_MITIGATION_ROUNDS
        and not (breaker is not None and breaker.is_open("ClientAdvisor"))
    ):
        return "ClientAdvisor", RULE_MITIGATE

    return REPORTER, RULE_REPORT
```

Plain LangGraph, `route()` in `examples/langgraph_comparison/plain_routing.py`:

```python
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
```

The order of the rules is the same: halt, then required specialists, then one bounded
mitigation round, then the reporter. The differences are in what each function reads and
what happens around it.

## What OrchestratorX does differently

**Severity is decided once and stored in state.** The plain `route()` recomputes the worst
severity from `findings` on every call. In OrchestratorX the supervisor calls
`decide_escalation()` (the `=== ESCALATION SEAM ===` in `src/orchestratorx/policy.py`) and
writes the result to the `escalation` and `halted` fields; `_route()` only reads those
fields. The escalation policy can then change without touching the routing function.

**One node routes; the others cannot.** In `Orchestrator._build()`
(`src/orchestratorx/orchestrator.py`) only the supervisor has a conditional edge, and it
follows `state["next_agent"]`. Every specialist has a fixed edge back to the supervisor. In
the plain version, `route()` is attached to every node, and its target map lets any of those
nodes go to any of the four nodes, including back to itself.

**Every chosen hop is checked: `validate_transition`.** After `next_hop` picks a target, the
supervisor calls `validate_transition(SUPERVISOR, target)`, which raises
`PolicyViolationError` if the target is not in `LEGAL_TRANSITIONS[SUPERVISOR]`
(`src/orchestratorx/policy.py`). The supervisor catches that error, records it, and ends the
run at the `Reporter` with `halted` set, rather than following the illegal hop. The plain
version has no such check: whatever `route()` returns, the graph follows, as long as the
name is in the target map.

**The cycle has a budget: `MAX_HOPS`.** `check_budget()` raises `BudgetExceededError` once
`visited` reaches `MAX_HOPS` (12) entries, and the supervisor again ends the run at the
`Reporter` with the error recorded. `test_budget_stops_a_runaway_cycle` in
`tests/test_routing.py` covers this. The plain version has no budget of its own. Its only
backstop is LangGraph's `recursion_limit`. In the installed `langgraph` 1.2.11 the default
is `10007` (`langgraph/_internal/_config.py`, overridable with
`LANGGRAPH_DEFAULT_RECURSION_LIMIT`). I confirmed it with a throwaway graph whose edge always
loops back to the same node: `invoke()` raised `GraphRecursionError: Recursion limit of
10007 reached without hitting a stop condition`. So a routing bug there ends in an exception
after thousands of steps, not in a report. A caller can pass a lower
`{"recursion_limit": N}`, but nothing in the example does.

**Failing specialists are routed around: the circuit breaker.** Each run gets its own
`CircuitBreaker` (`Orchestrator.breaker_for(run_id)`). Once a specialist has failed
`threshold` times, `_route()` skips it (`test_open_breaker_routes_around_a_specialist` in
`tests/test_routing.py`). The plain version has no failure handling: nothing in
`plain_routing.py` catches an exception raised by a node.

**Each decision can be explained afterwards: the route trace.** With
`Orchestrator(trace_routes=True)`, or `orchestratorx demo --trace`, the supervisor calls
`traced_next_hop()`. For every hop it records the rule that fired (`RULE_*` in
`src/orchestratorx/policy.py`) and exactly the state fields and breaker results the rules
read (`src/orchestratorx/route_trace.py`). It writes them to `traces/<run_id>.json` and
`.mmd`. `docs/samples/` holds one unedited trace. The plain version can show which nodes ran
(`visited`) but not which rule chose each one or what it was looking at.

## Limits

What this comparison does **not** show:

- **No benchmark.** Nothing here was timed, and no performance claim is made in either
  direction.
- **One scenario only.** The example reproduces the "Concentrated and over-levered" path. The
  clean and critical-halt paths are not run through the plain version, and nothing tests that
  the two `route()` functions agree on every possible input.
- **The plain version is a mirror, not a reference.** The same author wrote it to follow
  OrchestratorX's rules. It is not an independent or typical LangGraph application, and a
  LangGraph user can add their own validation, budget, or logging around a conditional edge.
- **Synthetic data, no real model.** The example's findings are fixed stubs. The OrchestratorX
  demo uses synthetic portfolios (`src/orchestratorx/cli.py`) and the offline `FakeLLM`. No run
  with a real model (Claude or Gemini) was made for this note.
- **No prompt-routed baseline.** The case against routing by prompt text is a design argument,
  not a measured result. No prompt-routed version was built or run.
- **The example is linted but not tested in CI.** CI runs `ruff check .` and
  `ruff format --check .` on the whole repo, which includes `examples/`. `pytest` only collects
  `tests/` (`testpaths` in `pyproject.toml`), so `test_plain_routing.py` runs only when invoked
  directly.
