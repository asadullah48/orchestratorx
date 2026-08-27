"""Audit trail for a run.

The locked requirement was that *every state transition must serialize to a
trace*. That is stricter than logging: a log records what code chose to
mention, whereas this records one event per node execution, in order, whether
the node succeeded or not. If a hop happened, it is in here.

The trace is deliberately kept outside the state. Putting it inside would make
it subject to the same reducers as everything else, and a partially-merged
audit trail is worse than none.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One node execution."""

    step: int
    node: str
    at: str
    escalation: str = ""
    findings_added: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunTrace:
    """Ordered record of everything one run did."""

    run_id: str
    objective: str = ""
    started_at: str = field(default_factory=_now_iso)
    events: list[TraceEvent] = field(default_factory=list)

    def record(
        self,
        node: str,
        *,
        escalation: str = "",
        findings_added: int = 0,
        duration_ms: float = 0.0,
        error: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            step=len(self.events),
            node=node,
            at=_now_iso(),
            escalation=escalation,
            findings_added=findings_added,
            duration_ms=round(duration_ms, 3),
            error=error,
            detail=dict(detail or {}),
        )
        self.events.append(event)
        return event

    @property
    def path(self) -> list[str]:
        """The nodes this run actually visited, in order."""
        return [e.node for e in self.events]

    @property
    def failed_steps(self) -> list[TraceEvent]:
        return [e for e in self.events if e.error]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "objective": self.objective,
            "started_at": self.started_at,
            "steps": len(self.events),
            "path": self.path,
            "events": [e.to_dict() for e in self.events],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def write_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json() + "\n", encoding="utf-8")
        return target


TRACES: dict[str, RunTrace] = {}
"""Process-local registry so the API can serve ``GET /trace/{run_id}``.

In memory on purpose: persistence is an operator's choice, and ``write_json``
is the export seam.
"""


def register(run_trace: RunTrace) -> RunTrace:
    TRACES[run_trace.run_id] = run_trace
    return run_trace


def get(run_id: str) -> RunTrace | None:
    return TRACES.get(run_id)
