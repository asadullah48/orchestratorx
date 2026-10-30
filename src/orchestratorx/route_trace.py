"""Route trace: why the supervisor sent each hop where it did.

The run trace (``trace.py``) records *that* a node ran. This records *why* the
supervisor picked it: the rule that fired and the exact inputs ``next_hop()``
read to decide. ``observed`` is captured while routing runs, never rebuilt
afterwards, so it cannot drift from the code.

Run metadata is kept apart from the entries so two runs of the same input can
be compared with :meth:`RouteTrace.comparable`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from orchestratorx.trace import _now_iso


class RouteTraceEntry(BaseModel):
    """One supervisor decision."""

    step: int
    from_node: str
    to_node: str
    rule: str
    observed: dict[str, Any] = Field(default_factory=dict)


class RouteTraceMeta(BaseModel):
    """Varies between runs of the same input; excluded from comparisons."""

    run_id: str
    started_at: str = Field(default_factory=_now_iso)
    finished_at: str = ""


class RouteTrace(BaseModel):
    """Every routing decision of one run, in order."""

    meta: RouteTraceMeta
    entries: list[RouteTraceEntry] = Field(default_factory=list)

    def record(self, from_node: str, to_node: str, rule: str, observed: dict[str, Any]) -> None:
        self.entries.append(
            RouteTraceEntry(
                step=len(self.entries) + 1,
                from_node=from_node,
                to_node=to_node,
                rule=rule,
                observed=dict(observed),
            )
        )

    def comparable(self) -> list[dict[str, Any]]:
        """The entries only, without run id or timestamps."""
        return [e.model_dump(mode="json") for e in self.entries]

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True)

    def to_mermaid(self) -> str:
        lines = ["flowchart LR"]
        for e in self.entries:
            lines.append(f"    {e.from_node} -->|{e.step}: {e.rule}| {e.to_node}")
        return "\n".join(lines) + "\n"

    def write(self, directory: str | Path = "traces") -> tuple[Path, Path]:
        """Write ``<run_id>.json`` and ``<run_id>.mmd`` into ``directory``."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        json_path = target / f"{self.meta.run_id}.json"
        mmd_path = target / f"{self.meta.run_id}.mmd"
        json_path.write_text(self.to_json() + "\n", encoding="utf-8")
        mmd_path.write_text(self.to_mermaid(), encoding="utf-8")
        return json_path, mmd_path
