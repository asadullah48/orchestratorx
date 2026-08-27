"""Typed orchestration state.

The graph **loops**: the supervisor routes to a specialist, the specialist
returns, and control goes back to the supervisor to pick the next hop. That is
what makes reducers necessary here. Any field written on more than one visit is
declared ``Annotated[list[...], operator.add]`` so appends accumulate; without
that, each specialist would silently overwrite the previous one's findings.

``visited`` is append-only for the same reason it is useful: it turns
"the supervisor never skips ComplianceChecker" into an assertion a test can
make, rather than a claim in a README.
"""

from __future__ import annotations

import operator
import uuid
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


class Severity(StrEnum):
    """Ordered from least to most serious."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_ORDER.index(self)


_SEVERITY_ORDER = [
    Severity.INFO,
    Severity.LOW,
    Severity.MEDIUM,
    Severity.HIGH,
    Severity.CRITICAL,
]


class Escalation(StrEnum):
    """What the supervisor decided to do about the findings so far."""

    CONTINUE = "continue"
    """Annotate and keep going to the reporter."""

    MITIGATE = "mitigate"
    """Route to ClientAdvisor for a mitigation proposal, then re-check."""

    HALT = "halt"
    """Stop the run; the report is not safe to produce."""


class Finding(BaseModel):
    """One thing a specialist noticed that the supervisor may need to act on."""

    code: str
    severity: Severity
    message: str
    remediation: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class AgentResult(BaseModel):
    """What a specialist hands back to the supervisor."""

    agent: str
    ok: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    latency_ms: float = 0.0
    attempts: int = 1
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class OrchestrationState(TypedDict, total=False):
    """State carried between graph nodes.

    Scalar fields are last-write-wins. List fields carry ``operator.add``
    reducers because the loop revisits nodes.
    """

    run_id: str
    objective: str
    request: dict[str, Any]

    next_agent: str
    visited: Annotated[list[str], operator.add]
    results: Annotated[list[dict[str, Any]], operator.add]
    findings: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]

    escalation: str
    mitigation_rounds: int
    halted: bool
    report: str


def initial_state(objective: str, request: dict[str, Any] | None = None) -> OrchestrationState:
    """Build a well-formed starting state.

    Every list field is seeded explicitly so reducers have something to append
    to and nodes never have to guard against a missing key.
    """
    return OrchestrationState(
        run_id=new_run_id(),
        objective=objective,
        request=dict(request or {}),
        next_agent="",
        visited=[],
        results=[],
        findings=[],
        errors=[],
        escalation=Escalation.CONTINUE.value,
        mitigation_rounds=0,
        halted=False,
        report="",
    )


def max_severity(findings: list[dict[str, Any]]) -> Severity | None:
    """Highest severity present, or ``None`` when there are no findings."""
    if not findings:
        return None
    return max((Severity(f["severity"]) for f in findings), key=lambda s: s.rank)
