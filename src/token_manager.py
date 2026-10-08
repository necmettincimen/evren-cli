"""
Token estimation and context-budget management for EVREN CLI.

Ports the C# `TokenManager.cs` behaviour:
- Character-based estimation (~3.5 chars/token) as a fast heuristic.
- Calibration against the server-reported `usage` so estimates converge.
- Reasoning-token budget: reasoning models share a single `max_tokens` between
  the hidden chain-of-thought and the visible answer, so the reserve must account
  for both. This replaces the old `tokens < 2048 -> 4096` hack with a real rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Average characters per token for mixed Turkish/English/code text.
DEFAULT_CHARS_PER_TOKEN = 3.5

# Models that emit a hidden reasoning stream sharing the max_tokens budget.
REASONING_MODELS = {"deepseek-v4.1-flash", "glm-5.3"}

# Minimum output budget for reasoning models (thinking + answer).
REASONING_MIN_OUTPUT_TOKENS = 4096

# Default context window assumption when the model is unknown.
DEFAULT_CONTEXT_WINDOW = 128_000


@dataclass
class TokenEstimate:
    """A token estimate with its source for transparency."""

    tokens: int
    method: str  # "heuristic" | "calibrated"


class TokenManager:
    """Estimates token usage and calibrates against server-reported usage."""

    def __init__(
        self,
        chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
    ):
        self.chars_per_token = chars_per_token
        self.context_window = context_window
        # Calibration ratio: actual_tokens / estimated_tokens. Starts at 1.0.
        self._calibration = 1.0
        self._calibration_samples = 0

    # ---- estimation ----------------------------------------------------

    def estimate_text(self, text: str) -> int:
        """Estimates tokens for a plain string."""
        if not text:
            return 0
        raw = len(text) / self.chars_per_token
        return max(1, int(raw * self._calibration))

    def estimate_message(self, message: dict[str, Any]) -> int:
        """Estimates tokens for a single chat message (content + tool calls)."""
        total = 0
        content = message.get("content")
        if isinstance(content, str):
            total += self.estimate_text(content)

        # Tool call arguments count toward the prompt too.
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            total += self.estimate_text(fn.get("name", ""))
            total += self.estimate_text(fn.get("arguments", ""))

        # Small per-message overhead (role, delimiters).
        total += 4
        return total

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """Estimates total prompt tokens for a message list."""
        return sum(self.estimate_message(m) for m in messages)

    # ---- calibration ---------------------------------------------------

    def calibrate(self, estimated: int, actual: int) -> None:
        """Adjusts the calibration ratio using server-reported usage.

        Uses a simple running average so a single outlier does not skew results.
        """
        if estimated <= 0 or actual <= 0:
            return
        ratio = actual / estimated
        # Exponential moving average (alpha = 0.3).
        alpha = 0.3
        self._calibration = (1 - alpha) * self._calibration + alpha * ratio
        self._calibration_samples += 1

    def calibrate_from_response(self, estimated: int, response: Any) -> None:
        """Calibrates using the `usage` object of an OpenAI-style response."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        if prompt_tokens:
            self.calibrate(estimated, prompt_tokens)

    @property
    def calibration(self) -> float:
        return self._calibration

    # ---- output budget -------------------------------------------------

    def resolve_output_budget(self, model: str, requested: int | None) -> int:
        """Resolves the effective max_tokens for a model.

        Reasoning models need a larger minimum because thinking + answer share
        the same budget.
        """
        budget = requested or 0
        if model in REASONING_MODELS:
            return max(budget, REASONING_MIN_OUTPUT_TOKENS)
        return budget

    # ---- context budget ------------------------------------------------

    def remaining_context(self, messages: list[dict[str, Any]], output_budget: int) -> int:
        """Returns how many prompt tokens remain before the context window fills."""
        used = self.estimate_messages(messages)
        return self.context_window - used - output_budget

    def fits(self, messages: list[dict[str, Any]], output_budget: int) -> bool:
        """True if the messages + output budget fit within the context window."""
        return self.remaining_context(messages, output_budget) > 0
