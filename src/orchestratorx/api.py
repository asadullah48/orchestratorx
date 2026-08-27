"""FastAPI surface.

Two endpoints, deliberately: start a run, and read back exactly what it did.
The second is the point — an orchestrator you cannot interrogate after the
fact is not auditable, however good its logging is.

Requires the ``api`` extra::

    pip install "orchestratorx[api]"
    uvicorn orchestratorx.api:app --reload
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from orchestratorx import __version__
from orchestratorx.llm import get_client
from orchestratorx.orchestrator import Orchestrator
from orchestratorx.policy import MAX_HOPS, MAX_MITIGATION_ROUNDS, REQUIRED_SPECIALISTS
from orchestratorx.trace import get as get_trace

app = FastAPI(
    title="OrchestratorX",
    version=__version__,
    description="Supervisor-pattern multi-agent orchestration with an auditable trace.",
)

# One compiled graph for the process. Runs are isolated by run_id, so sharing
# it across requests is safe and avoids recompiling per call.
_orchestrator = Orchestrator()


class RunRequest(BaseModel):
    objective: str = Field(description="What this run is for.")
    request: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain payload, e.g. {client, leverage, positions[]}.",
    )


class RunResponse(BaseModel):
    run_id: str
    escalation: str
    halted: bool
    findings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    path: list[str]
    report: str


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "reasoner": get_client().name,
        "required_specialists": list(REQUIRED_SPECIALISTS),
        "max_hops": MAX_HOPS,
        "max_mitigation_rounds": MAX_MITIGATION_ROUNDS,
    }


@app.post("/run", response_model=RunResponse)
def run(body: RunRequest) -> RunResponse:
    final, run_trace = _orchestrator.run(body.objective, body.request)
    return RunResponse(
        run_id=final["run_id"],
        escalation=final.get("escalation", ""),
        halted=bool(final.get("halted")),
        findings=final.get("findings", []),
        errors=final.get("errors", []),
        path=run_trace.path,
        report=final.get("report", ""),
    )


@app.get("/trace/{run_id}")
def trace(run_id: str) -> dict[str, Any]:
    found = get_trace(run_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    return found.to_dict()
