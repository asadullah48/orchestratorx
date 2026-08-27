"""Provider-agnostic LLM access with a deterministic default.

Two rules hold this design together:

1. **The default is offline.** ``FakeLLM`` needs no key and no network, so
   ``pytest`` and every example run on a fresh clone. That is what makes this a
   credible portfolio repo rather than a demo that only works on one laptop.
2. **The model is never load-bearing.** Specialists compute their findings in
   plain Python and use the model only for narrative prose. A routing decision
   that depended on a live model would make "the supervisor never skips
   ComplianceChecker" unprovable in CI.

Optional providers are imported lazily, so neither SDK is a hard dependency.
"""

from __future__ import annotations

import hashlib
import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Minimal surface the framework depends on."""

    name: str

    def complete(self, prompt: str, **kw: object) -> str: ...


class FakeLLM:
    """Deterministic, offline, dependency-free.

    Output derives from a digest of the prompt, so it is stable across runs and
    machines — a test can assert on it without pinning a model version.
    """

    name = "fake"

    def complete(self, prompt: str, **kw: object) -> str:
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
        first_line = prompt.strip().splitlines()[0] if prompt.strip() else "(empty prompt)"
        return f"[fake:{digest}] {first_line[:160]}"


class ClaudeClient:
    """Anthropic backend. Selected by ``ORCHESTRATORX_LLM=claude``."""

    name = "claude"

    def __init__(self, model: str = "claude-opus-5") -> None:
        import anthropic  # lazy: keeps the base install dependency-free

        self._client = anthropic.Anthropic()
        self._model = model
        self._fallback = FakeLLM()

    def complete(self, prompt: str, **kw: object) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                thinking={"type": "adaptive"},
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001 - narration must never break a run
            pass
        return self._fallback.complete(prompt)


class GeminiClient:
    """Google backend. Selected by ``ORCHESTRATORX_LLM=gemini``."""

    name = "gemini"

    def __init__(self, model: str = "gemini-2.0-flash") -> None:
        from google import genai  # lazy

        self._client = genai.Client()
        self._model = model
        self._fallback = FakeLLM()

    def complete(self, prompt: str, **kw: object) -> str:
        try:
            response = self._client.models.generate_content(model=self._model, contents=prompt)
            text = (getattr(response, "text", "") or "").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001 - narration must never break a run
            pass
        return self._fallback.complete(prompt)


def get_client(backend: str | None = None) -> LLMClient:
    """Resolve a client from an explicit name or the environment.

    Anything unavailable degrades to :class:`FakeLLM` rather than raising, so a
    missing key costs prose quality and never a failed run.
    """
    choice = (backend or os.getenv("ORCHESTRATORX_LLM") or "fake").strip().lower()
    model = os.getenv("ORCHESTRATORX_MODEL", "")

    if choice == "claude" and os.getenv("ANTHROPIC_API_KEY"):
        try:
            return ClaudeClient(model or "claude-opus-5")
        except Exception:  # noqa: BLE001 - the `claude` extra is not installed
            return FakeLLM()

    if choice == "gemini" and os.getenv("GOOGLE_API_KEY"):
        try:
            return GeminiClient(model or "gemini-2.0-flash")
        except Exception:  # noqa: BLE001 - the `gemini` extra is not installed
            return FakeLLM()

    return FakeLLM()
