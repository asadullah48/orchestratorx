# Demo video storyboard (60–90 s) — plan only, not recorded

| Time | Source | On screen | Voice-over |
|---|---|---|---|
| 0–20 s | **ANIMATED DIAGRAM** (`architecture-animation.html`, first 20 s) | Hook, graph, typed state, hops 1–3 | "Most agent systems hide routing inside a prompt. OrchestratorX keeps it in typed state. One supervisor reads that state and picks the next agent." |
| 20–70 s | **REAL SCREEN RECORDING** — VS Code terminal | Type the command; pause on run [2]'s `path` line | "This is a real run. No API keys, no network. Run two has high findings, so the supervisor sends it to ClientAdvisor once, then on to the Reporter. Run three breaks a hard rule, so it halts." |
| 70–90 s | **REAL SCREEN RECORDING** — test suite | Run the tests; show the final pass line as it really appears | "The routing rule is plain Python, so the tests check it on every commit." |

Commands to type (from the README):

```bash
.venv/Scripts/python -m orchestratorx.cli demo
.venv/Scripts/python -m pytest
```

Expected route for run [2] (as printed by the demo):
`supervisor -> RiskModeler -> supervisor -> ComplianceChecker -> supervisor -> ClientAdvisor -> supervisor -> Reporter`

Keep the corner label "Animated diagram — not a recording of a live run" on the first segment only.
