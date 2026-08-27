"""Command-line entry point.

orchestratorx demo     run three portfolios through the supervisor
orchestratorx graph    print the graph as mermaid
orchestratorx policy   print the routing and escalation policy in force
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from orchestratorx import __version__
from orchestratorx.agents import CONCENTRATION_LIMIT, LEVERAGE_LIMIT, RESTRICTED_SECTORS
from orchestratorx.llm import get_client
from orchestratorx.orchestrator import Orchestrator, mermaid
from orchestratorx.policy import (
    LEGAL_TRANSITIONS,
    MAX_HOPS,
    MAX_MITIGATION_ROUNDS,
    REQUIRED_SPECIALISTS,
)
from orchestratorx.state import OrchestrationState
from orchestratorx.trace import RunTrace

RULE = "-" * 72

CLEAN_BOOK: dict[str, Any] = {
    "client": "ACME-PENSION-01",
    "leverage": 1.0,
    "positions": [
        {"symbol": "UTL", "issuer": "Issuer One", "sector": "utilities", "weight": 0.20},
        {"symbol": "HLT", "issuer": "Issuer Two", "sector": "healthcare", "weight": 0.20},
        {"symbol": "CNS", "issuer": "Issuer Three", "sector": "consumer", "weight": 0.20},
        {"symbol": "FIN", "issuer": "Issuer Four", "sector": "financials", "weight": 0.20},
        {"symbol": "ENR", "issuer": "Issuer Five", "sector": "energy", "weight": 0.20},
    ],
}

CONCENTRATED_BOOK: dict[str, Any] = {
    "client": "ACME-PENSION-02",
    "leverage": 2.5,
    "positions": [
        {"symbol": "AAA", "issuer": "Issuer Alpha", "sector": "technology", "weight": 0.60},
        {"symbol": "BBB", "issuer": "Issuer Beta", "sector": "utilities", "weight": 0.40},
    ],
}

RESTRICTED_BOOK: dict[str, Any] = {
    "client": "ACME-PENSION-03",
    "leverage": 1.0,
    "positions": [
        {"symbol": "TOB", "issuer": "Issuer Six", "sector": "tobacco", "weight": 0.20},
        {"symbol": "UTL", "issuer": "Issuer Seven", "sector": "utilities", "weight": 0.20},
        {"symbol": "HLT", "issuer": "Issuer Eight", "sector": "healthcare", "weight": 0.20},
        {"symbol": "CNS", "issuer": "Issuer Nine", "sector": "consumer", "weight": 0.20},
        {"symbol": "FIN", "issuer": "Issuer Ten", "sector": "financials", "weight": 0.20},
    ],
}

SCENARIOS = [
    ("Compliant book", "Nothing to escalate; straight through to the report.", CLEAN_BOOK),
    (
        "Concentrated and over-levered",
        "High findings trigger one bounded mitigation round via ClientAdvisor.",
        CONCENTRATED_BOOK,
    ),
    (
        "Restricted sector held",
        "A critical breach halts the run; the report is withheld rather than dressed up.",
        RESTRICTED_BOOK,
    ),
]


def _print_run(index: int, title: str, blurb: str, final: OrchestrationState, tr: RunTrace) -> None:
    print(f"\n[{index}] {title}")
    print(f"    {blurb}")
    print(f"    path       : {' -> '.join(tr.path)}")
    print(f"    escalation : {final.get('escalation')}  |  halted: {final.get('halted')}")
    findings = final.get("findings", [])
    if findings:
        codes = ", ".join(f"{f['code']}({f['severity']})" for f in findings)
        print(f"    findings   : {codes}")
    else:
        print("    findings   : none")
    if final.get("errors"):
        print(f"    errors     : {[e['type'] for e in final['errors']]}")
    print(f"    hops       : {len(tr.events)} (budget {MAX_HOPS})")


def cmd_demo(args: argparse.Namespace) -> int:
    reasoner = get_client(args.llm)
    orchestrator = Orchestrator(llm=reasoner)

    print(RULE)
    print(f"OrchestratorX {__version__} - supervisor-pattern multi-agent demo")
    print(f"  reasoner   : {reasoner.name}")
    print(f"  required   : {', '.join(REQUIRED_SPECIALISTS)}")
    print(f"  hop budget : {MAX_HOPS}   mitigation rounds: {MAX_MITIGATION_ROUNDS}")
    print(RULE)

    last: RunTrace | None = None
    for index, (title, blurb, book) in enumerate(SCENARIOS, start=1):
        final, tr = orchestrator.run("Review the book against the mandate", book)
        _print_run(index, title, blurb, final, tr)
        last = tr
        if args.reports:
            print("\n" + final.get("report", ""))

    print(f"\n{RULE}")
    print("Every hop above is recorded in an ordered trace, including failures.")
    print(RULE)

    if args.trace_out and last is not None:
        path = last.write_json(args.trace_out)
        print(f"wrote the last run's trace to {path}")
    return 0


def cmd_graph(_args: argparse.Namespace) -> int:
    print(mermaid())
    return 0


def cmd_policy(_args: argparse.Namespace) -> int:
    payload = {
        "required_specialists": list(REQUIRED_SPECIALISTS),
        "max_hops": MAX_HOPS,
        "max_mitigation_rounds": MAX_MITIGATION_ROUNDS,
        "legal_transitions": {k: sorted(v) for k, v in LEGAL_TRANSITIONS.items()},
        "mandate": {
            "concentration_limit": CONCENTRATION_LIMIT,
            "leverage_limit": LEVERAGE_LIMIT,
            "restricted_sectors": sorted(RESTRICTED_SECTORS),
        },
        "escalation": {
            "critical": "halt",
            "high": f"mitigate (max {MAX_MITIGATION_ROUNDS} round)",
            "medium_or_lower": "continue",
        },
    }
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestratorx",
        description="Supervisor-pattern multi-agent orchestration with an audit trail.",
    )
    parser.add_argument("--version", action="version", version=f"orchestratorx {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run three portfolios through the supervisor")
    demo.add_argument("--llm", default=None, choices=["fake", "claude", "gemini"])
    demo.add_argument("--reports", action="store_true", help="print the full report text")
    demo.add_argument(
        "--trace-out",
        nargs="?",
        const="orchestratorx-trace.json",
        default=None,
        help="write the last run's trace to a JSON file",
    )
    demo.set_defaults(func=cmd_demo)

    sub.add_parser("graph", help="print the graph as mermaid").set_defaults(func=cmd_graph)
    sub.add_parser("policy", help="print the policy in force").set_defaults(func=cmd_policy)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
