"""OrchestratorX — a supervisor-pattern multi-agent framework with an audit trail.

Typical use::

    from orchestratorx import Orchestrator

    final, trace = Orchestrator().run(
        "Review the book against the mandate",
        {"client": "ACME-PENSION-01", "leverage": 1.4, "positions": [...]},
    )
    print(final["report"])
    print(trace.path)

Runs offline by default: the reasoner is a deterministic ``FakeLLM`` unless a
provider is explicitly configured, so ``pytest`` and every example work on a
fresh clone with no keys.
"""

from orchestratorx.orchestrator import Orchestrator, mermaid, run_once
from orchestratorx.state import (
    AgentResult,
    Escalation,
    Finding,
    OrchestrationState,
    Severity,
    initial_state,
)
from orchestratorx.trace import RunTrace, TraceEvent

__version__ = "0.1.0"

__all__ = [
    "AgentResult",
    "Escalation",
    "Finding",
    "OrchestrationState",
    "Orchestrator",
    "RunTrace",
    "Severity",
    "TraceEvent",
    "__version__",
    "initial_state",
    "mermaid",
    "run_once",
]
