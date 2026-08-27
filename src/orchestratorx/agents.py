"""The specialist agents.

Every finding below is computed in plain Python. The LLM is used only where a
human wants prose — the report narrative — and never to decide whether a
portfolio is compliant. That split is what lets the whole suite run offline and
makes each finding reproducible rather than merely plausible.

All market and client data here is synthetic.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol, runtime_checkable

from orchestratorx.errors import AgentFailureError
from orchestratorx.llm import FakeLLM, LLMClient
from orchestratorx.state import AgentResult, Finding, OrchestrationState, Severity

# --------------------------------------------------------------------------
# Domain constants (synthetic)
# --------------------------------------------------------------------------

RESTRICTED_SECTORS = frozenset({"tobacco", "gambling", "weapons", "adult"})
"""Sectors an ethically screened mandate may not hold at all."""

SECTOR_VOLATILITY: dict[str, float] = {
    "technology": 0.28,
    "energy": 0.24,
    "financials": 0.21,
    "healthcare": 0.17,
    "utilities": 0.12,
    "consumer": 0.15,
    "tobacco": 0.19,
    "gambling": 0.31,
    "weapons": 0.22,
}
DEFAULT_VOLATILITY = 0.20

CONCENTRATION_LIMIT = 0.25
"""Maximum weight in a single issuer before it becomes a finding."""

LEVERAGE_LIMIT = 2.0


@runtime_checkable
class SpecialistAgent(Protocol):
    """What the supervisor needs from any specialist."""

    name: str

    def run(self, state: OrchestrationState) -> AgentResult: ...


class BaseAgent:
    """Shared timing and error wrapping.

    Subclasses implement :meth:`work`. Anything raised inside ``work`` is
    converted to :class:`AgentFailureError`, which is the only exception the
    retry policy treats as worth another attempt.
    """

    name = "BaseAgent"

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm: LLMClient = llm or FakeLLM()

    def work(self, state: OrchestrationState) -> tuple[dict[str, Any], list[Finding]]:
        raise NotImplementedError

    def run(self, state: OrchestrationState) -> AgentResult:
        started = perf_counter()
        try:
            payload, findings = self.work(state)
        except AgentFailureError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalised for the retry policy
            raise AgentFailureError(str(exc), agent=self.name) from exc
        return AgentResult(
            agent=self.name,
            ok=True,
            payload=payload,
            findings=findings,
            latency_ms=(perf_counter() - started) * 1000,
        )


def _positions(state: OrchestrationState) -> list[dict[str, Any]]:
    positions = state.get("request", {}).get("positions") or []
    if not isinstance(positions, list):
        raise AgentFailureError("request.positions must be a list")
    return positions


# --------------------------------------------------------------------------
# RiskModeler
# --------------------------------------------------------------------------


class RiskModeler(BaseAgent):
    """Quantifies exposure. Reports numbers, not verdicts."""

    name = "RiskModeler"

    def work(self, state: OrchestrationState) -> tuple[dict[str, Any], list[Finding]]:
        positions = _positions(state)
        if not positions:
            raise AgentFailureError("no positions to model", agent=self.name)

        total_weight = sum(float(p.get("weight", 0.0)) for p in positions)
        by_issuer: dict[str, float] = {}
        by_sector: dict[str, float] = {}
        for p in positions:
            weight = float(p.get("weight", 0.0))
            issuer = str(p.get("issuer", "unknown"))
            sector = str(p.get("sector", "unknown"))
            by_issuer[issuer] = by_issuer.get(issuer, 0.0) + weight
            by_sector[sector] = by_sector.get(sector, 0.0) + weight

        # Weighted-average sector volatility as a transparent risk proxy.
        portfolio_vol = sum(
            weight * SECTOR_VOLATILITY.get(sector, DEFAULT_VOLATILITY)
            for sector, weight in by_sector.items()
        )
        leverage = float(state.get("request", {}).get("leverage", 1.0))
        top_issuer, top_weight = max(by_issuer.items(), key=lambda kv: kv[1])
        adjusted_vol = portfolio_vol * leverage

        payload = {
            "positions": len(positions),
            "total_weight": round(total_weight, 4),
            "top_issuer": top_issuer,
            "top_issuer_weight": round(top_weight, 4),
            "portfolio_volatility": round(adjusted_vol, 4),
            "leverage": leverage,
            "by_sector": {k: round(v, 4) for k, v in sorted(by_sector.items())},
        }

        findings: list[Finding] = []
        if adjusted_vol > 0.30:
            findings.append(
                Finding(
                    code="RSK-001",
                    severity=Severity.MEDIUM,
                    message=(
                        f"Leverage-adjusted volatility {adjusted_vol:.2%} is above "
                        "the 30% comfort band"
                    ),
                    remediation="Reduce leverage or rotate into lower-volatility sectors",
                    source=self.name,
                )
            )
        return payload, findings


# --------------------------------------------------------------------------
# ComplianceChecker
# --------------------------------------------------------------------------


class ComplianceChecker(BaseAgent):
    """Applies mandate rules. This is where severity actually bites."""

    name = "ComplianceChecker"

    def work(self, state: OrchestrationState) -> tuple[dict[str, Any], list[Finding]]:
        positions = _positions(state)
        findings: list[Finding] = []

        total_weight = sum(float(p.get("weight", 0.0)) for p in positions)
        if abs(total_weight - 1.0) > 0.01:
            findings.append(
                Finding(
                    code="CMP-001",
                    severity=Severity.MEDIUM,
                    message=f"Position weights sum to {total_weight:.2%}, not 100%",
                    remediation="Rebalance so the book is fully and exactly allocated",
                    source=self.name,
                )
            )

        by_issuer: dict[str, float] = {}
        for p in positions:
            issuer = str(p.get("issuer", "unknown"))
            by_issuer[issuer] = by_issuer.get(issuer, 0.0) + float(p.get("weight", 0.0))
        for issuer, weight in sorted(by_issuer.items()):
            if weight > CONCENTRATION_LIMIT:
                findings.append(
                    Finding(
                        code="CMP-002",
                        severity=Severity.HIGH,
                        message=(
                            f"Concentration in {issuer} is {weight:.2%}, above the "
                            f"{CONCENTRATION_LIMIT:.0%} single-issuer limit"
                        ),
                        remediation="Trim the position or record a documented exception",
                        source=self.name,
                    )
                )

        breached = sorted(
            {
                str(p.get("sector", "")).lower()
                for p in positions
                if str(p.get("sector", "")).lower() in RESTRICTED_SECTORS
            }
        )
        for sector in breached:
            findings.append(
                Finding(
                    code="CMP-003",
                    severity=Severity.CRITICAL,
                    message=f"Mandate prohibits any exposure to {sector}",
                    remediation="Divest the holding before the next reporting date",
                    source=self.name,
                )
            )

        leverage = float(state.get("request", {}).get("leverage", 1.0))
        if leverage > LEVERAGE_LIMIT:
            findings.append(
                Finding(
                    code="CMP-004",
                    severity=Severity.HIGH,
                    message=f"Leverage {leverage:.2f}x exceeds the {LEVERAGE_LIMIT:.1f}x cap",
                    remediation="Deleverage to within the mandate cap",
                    source=self.name,
                )
            )

        payload = {
            "checks_run": 4,
            "breaches": len(findings),
            "restricted_sectors_found": breached,
        }
        return payload, findings


# --------------------------------------------------------------------------
# ClientAdvisor
# --------------------------------------------------------------------------

_MITIGATIONS: dict[str, str] = {
    "CMP-001": "Rebalance the book to exactly 100% allocated.",
    "CMP-002": "Trim the concentrated issuer to the 25% limit and redistribute pro rata.",
    "CMP-003": "Divest the restricted holding and quarantine the proceeds pending instruction.",
    "CMP-004": "Unwind leverage to 2.0x or below before the next valuation point.",
    "RSK-001": "Rotate a portion into utilities or healthcare to lower portfolio volatility.",
}


class ClientAdvisor(BaseAgent):
    """Proposes mitigations. Reached only when escalation asks for one."""

    name = "ClientAdvisor"

    def work(self, state: OrchestrationState) -> tuple[dict[str, Any], list[Finding]]:
        findings = state.get("findings", [])
        proposals = [
            {"code": f["code"], "severity": f["severity"], "action": _MITIGATIONS[f["code"]]}
            for f in findings
            if f["code"] in _MITIGATIONS
        ]
        payload = {
            "proposals": proposals,
            "unaddressed": [f["code"] for f in findings if f["code"] not in _MITIGATIONS],
        }
        # An advisor proposes; it never clears a finding on its own authority.
        return payload, []


# --------------------------------------------------------------------------
# Reporter
# --------------------------------------------------------------------------


class Reporter(BaseAgent):
    """Terminal node. Assembles the deliverable."""

    name = "Reporter"

    def work(self, state: OrchestrationState) -> tuple[dict[str, Any], list[Finding]]:
        findings = state.get("findings", [])
        halted = bool(state.get("halted"))
        results = {r["agent"]: r for r in state.get("results", [])}

        header = (
            "RUN HALTED - report withheld"
            if halted
            else f"Portfolio review for {state.get('request', {}).get('client', 'client')}"
        )
        lines = [header, f"Objective: {state.get('objective', '')}", ""]

        risk = results.get("RiskModeler", {}).get("payload", {})
        if risk:
            lines.append(
                f"Risk: {risk.get('positions')} positions, "
                f"volatility {risk.get('portfolio_volatility')}, "
                f"top issuer {risk.get('top_issuer')} at {risk.get('top_issuer_weight')}"
            )

        if findings:
            lines.append(f"Findings ({len(findings)}):")
            lines.extend(f"  - [{f['severity']}] {f['code']}: {f['message']}" for f in findings)
        else:
            lines.append("Findings: none.")

        advisor = results.get("ClientAdvisor", {}).get("payload", {})
        if advisor.get("proposals"):
            lines.append("Proposed mitigations:")
            lines.extend(f"  - {p['code']}: {p['action']}" for p in advisor["proposals"])

        narrative = self.llm.complete(
            "Summarise this portfolio review for a client in two sentences.\n" + "\n".join(lines)
        )
        lines += ["", f"Narrative: {narrative}"]

        report = "\n".join(lines)
        return {"report": report, "halted": halted, "finding_count": len(findings)}, []


def default_specialists(llm: LLMClient | None = None) -> dict[str, BaseAgent]:
    """The standard roster, keyed by node name."""
    client = llm or FakeLLM()
    return {
        RiskModeler.name: RiskModeler(client),
        ComplianceChecker.name: ComplianceChecker(client),
        ClientAdvisor.name: ClientAdvisor(client),
        Reporter.name: Reporter(client),
    }
