# OrchestratorX

**A LangGraph supervisor reference implementation for financial-portfolio workflows, with model-free routing, typed state, and ordered execution traces.**

It separates routing and escalation policy from model-generated narrative so specialist
ordering, mitigation, retries, and failure handling can be tested independently of a model.
RiskModeler, ComplianceChecker, and ClientAdvisor return control to the supervisor;
Reporter is terminal.

The default demo and 41-test suite run without API keys or model-provider access.

**Verify after the development install below:**

```bash
.venv/Scripts/python -m orchestratorx.cli demo
.venv/Scripts/python -m pytest
```

**Limits / non-goals:** Reference implementation using synthetic data and illustrative
mandate rules. No authentication, persistent trace storage, or streaming. Hop and retry
limits do not impose a timeout on blocked calls. Inspect run errors and traces alongside
report text; Reporter does not explicitly include specialist errors in its narrative.

*[العربية](./README.ar.md) · [Specification](./SPEC.md)*

---

## The idea

When routing is delegated to a model through a prompt, testing who runs, in what order,
and how failures are handled also depends on model behaviour. Explicit routing policy
makes those decisions testable independently of the narrative model.

OrchestratorX pulls routing out of the prompt and into typed state:

```python
def next_hop(state, breaker=None) -> str:
    if state.get("halted"):
        return REPORTER
    for agent in REQUIRED_SPECIALISTS:
        if agent not in state["visited"] and (breaker is None or not breaker.is_open(agent)):
            return agent
    ...
```

Because that function consults no model, its happy-path ordering can be tested offline:

```python
def test_compliance_checker_is_never_skipped_on_the_happy_path():
    ...
    assert seen.index("ComplianceChecker") < seen.index(REPORTER)
```

The LLM still writes the client-facing narrative. It just never decides whether a
portfolio is compliant.

---

## Quickstart

```bash
git clone https://github.com/asadullah48/orchestratorx && cd orchestratorx
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m orchestratorx.cli demo
```

Real output:

```
[1] Compliant book
    path       : supervisor -> RiskModeler -> supervisor -> ComplianceChecker -> supervisor -> Reporter
    escalation : continue  |  halted: False
    findings   : none

[2] Concentrated and over-levered
    path       : supervisor -> RiskModeler -> supervisor -> ComplianceChecker -> supervisor -> ClientAdvisor -> supervisor -> Reporter
    escalation : continue  |  halted: False
    findings   : RSK-001(medium), CMP-002(high), CMP-002(high), CMP-004(high)

[3] Restricted sector held
    path       : supervisor -> RiskModeler -> supervisor -> ComplianceChecker -> supervisor -> Reporter
    escalation : halt  |  halted: True
    findings   : CMP-003(critical)
```

Note run 2: the HIGH findings routed to `ClientAdvisor` **exactly once**, then the bounded
counter flipped escalation back to `continue` and it terminated. Run 3 never reached the
advisor at all — a critical breach halts rather than shopping for a mitigation.

---

## The graph

```mermaid
flowchart TD
    START([run]) --> S[supervisor]
    S -->|next_agent| RM[RiskModeler]
    S -->|next_agent| CC[ComplianceChecker]
    S -->|escalation = mitigate| CA[ClientAdvisor]
    S -->|required done| RP[Reporter]
    RM --> S
    CC --> S
    CA --> S
    RP --> DONE([report])
```

| Agent | Job |
|---|---|
| **supervisor** | The only node that routes. Reads typed state, picks the next hop, enforces legal transitions. |
| **RiskModeler** | Quantifies exposure — concentration, sector mix, leverage-adjusted volatility. Reports numbers, not verdicts. |
| **ComplianceChecker** | Applies mandate rules. This is where severity bites. |
| **ClientAdvisor** | Proposes mitigations. Reached only when escalation asks. Never clears a finding on its own authority. |
| **Reporter** | Terminal node. Assembles the deliverable, or explains why it was withheld. |

RiskModeler, ComplianceChecker, and ClientAdvisor return **only** to the supervisor.
Reporter is terminal. Required-specialist ordering applies on the happy path; a halt or
an open breaker can bypass a specialist. That is what keeps
every routing decision in one testable function.

---

## Three things that make it hold together

**Reducers, because the graph loops.** `supervisor → specialist → supervisor` revisits
nodes. Append-only lists such as findings and visited nodes use
`Annotated[list, operator.add]` so updates accumulate. Scalar fields use replacement
updates; they do not all need list reducers.

**A hop budget, not just a retry cap.** Retries bound one node's failures; only a budget
bounds the *cycle*. "Route back for mitigation, then re-check" is otherwise a legal
infinite loop that no single component is misbehaving to cause.

**Per-run isolation.** The trace and circuit breaker are looked up by `run_id`, not shared across runs. Breakers are stored on the orchestrator in a dictionary keyed by
`run_id`. The concurrent-run test checks separate findings and run IDs; another test checks
separate traces. Failure injection across concurrent breakers is not covered by those tests.

---

## Escalation policy

Specialists report facts. What the supervisor *does* about them is a risk posture, isolated
in one function marked `=== ESCALATION SEAM ===` in `policy.py`:

| Worst finding | Action | Why |
|---|---|---|
| `critical` | **halt** | A report built on a critical breach is worse than no report — it looks authoritative. |
| `high` | **mitigate**, once | Route to ClientAdvisor for a proposal, then re-evaluate escalation over retained findings. Domain checks are not rerun against a changed portfolio. |
| `medium` or lower | **continue** | Annotate and report. |

Escalation decisions are centralized in `decide_escalation()`. Changing the number of
mitigation rounds also requires reviewing `MAX_MITIGATION_ROUNDS`, which routing uses,
and the hop budget. Proposed mitigations do not clear the original findings.

---

## Reliability

| Failure | Behaviour |
|---|---|
| Transient specialist error | Retried up to `max_attempts`; the run completes and `attempts` is recorded |
| Specialist exhausts retries | Error enters run state and trace; breaker failure count increases and opens at its configured threshold |
| Illegal transition | `PolicyViolationError`, **never retried** — retrying a deterministic error reproduces it |
| Runaway cycle | Supervisor detects `MAX_HOPS` and routes to Reporter with `BudgetExceededError` recorded |

These limits bound steps and retry attempts, not wall-clock duration. A blocked call
still needs a timeout. Handled specialist failures remain in run state and the trace,
but Reporter does not explicitly render those errors in its report text. Read both
before treating a report as complete.

---

## HTTP API

```bash
pip install -e ".[api]"
uvicorn orchestratorx.api:app --reload
```

| Endpoint | Purpose |
|---|---|
| `GET /health` | Version, active reasoner, policy in force |
| `POST /run` | Run an orchestration, return findings and the report |
| `GET /trace/{run_id}` | Read back exactly what that run did, hop by hop |

The third endpoint is the point. An orchestrator you can't interrogate after the fact
isn't auditable, however good its logging is.

---

## Commands

| Command | Does |
|---|---|
| `orchestratorx demo` | Three portfolios through the supervisor |
| `orchestratorx demo --reports` | Same, with full report text |
| `orchestratorx demo --trace-out t.json` | Export the last run's trace |
| `orchestratorx graph` | Print the graph as mermaid |
| `orchestratorx policy` | Print the routing and escalation policy in force |

---

## Optional model backends

Deterministic `FakeLLM` by default. To use a real model for the narrative only:

```bash
ORCHESTRATORX_LLM=claude ANTHROPIC_API_KEY=... orchestratorx demo
ORCHESTRATORX_LLM=gemini GOOGLE_API_KEY=...    orchestratorx demo
```

Anything unavailable degrades to `FakeLLM` rather than raising. A missing key costs prose
quality, never a failed run.

---

## Development

```bash
.venv/Scripts/python -m pytest      # 41 tests
.venv/Scripts/python -m ruff check .
```

```
src/orchestratorx/
  state.py         typed state, reducers, Finding / AgentResult
  policy.py        routing, retry, circuit breaker, escalation seam
  agents.py        RiskModeler, ComplianceChecker, ClientAdvisor, Reporter
  orchestrator.py  supervisor node + graph assembly
  trace.py         ordered audit trail
  llm.py           FakeLLM default, optional Claude / Gemini
  api.py           FastAPI service
  cli.py           command-line entry point
```

---

## Status and limits

A reference implementation, and the boundaries are deliberate: no auth, no persistence
(the trace registry is in-process, with `write_json` as the export seam), no streaming, and
the domain rules are illustrative rather than a real mandate. All client, issuer, and
market data is synthetic. This is not evidence of a customer production deployment.
The trace is an in-process execution record, not a durable compliance audit system.

## 🤖 Agentic AI Alignment

- **Autonomy** — the supervisor decides who runs next and when to stop
  from typed state alone (`next_hop()`), not from a prompt — routing is a
  testable function, not a hope about what the model will do.
- **Resilience** — a hop budget bounds the *cycle* (not just per-node
  retries), per-run isolation keeps concurrent runs' circuit breakers from
  leaking into each other, and a `PolicyViolationError` is never retried
  at the retry-policy boundary. Hop and retry limits do not time out blocked calls.
- **Adaptivity** — escalation decisions are centralized in `decide_escalation()`
  (`=== ESCALATION SEAM ===` in `policy.py`). Changes to mitigation rounds also
  require reviewing the shared round limit and hop budget.

### Roadmap

- Add a persistence layer for the trace registry (currently in-process,
  with `write_json` as the only export seam) so runs survive a restart.
- Extend the `FakeLLM`/Claude/Gemini backend seam to more providers, since
  the routing/reliability guarantees are already backend-agnostic.

## 🤖 Author

Built by **Asadullah Shafique**.

🔗 Explore my portfolio showcasing Agentic AI projects and real-world applications: [asadullahshafique-devunity.vercel.app](https://asadullahshafique-devunity.vercel.app)

## License

MIT
