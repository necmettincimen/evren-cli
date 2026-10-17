"""Model facts shared across the CLI (plan cli-stability-speed, item 7.5).

For now this is only the context-window table; the absolute budget
(`EVREN_CONTEXT_BUDGET`) is what actually limits the prompt.
"""

from __future__ import annotations

DEFAULT_CONTEXT_WINDOW = 128_000

# Substring -> context window in tokens. Matched against the model name, so
# provider-prefixed ids such as "zai/glm-5.3-fp8" resolve as well.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "qwen3.8-flash-next": 256_000,
    "deepseek-v4.1-flash": 1_000_000,
    "mimo-v2.6-pro": 1_000_000,
    "glm-5.3": 512_000,
}


def context_window_for(model: str | None) -> int:
    """Context window of `model`; 128K for unknown models."""
    name = (model or "").strip().lower()
    for key, window in MODEL_CONTEXT_WINDOWS.items():
        if key in name:
            return window
    return DEFAULT_CONTEXT_WINDOW
