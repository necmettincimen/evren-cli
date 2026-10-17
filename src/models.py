"""Model registry shared across the CLI (plan cli-stability-speed, 7.1 / 7.5).

Each entry records the roles a model is good for, its context window and
whether `thinking: false` / image input are safe. Config helpers
(planner/executor/thinking) read from here instead of hard-coding names.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_CONTEXT_WINDOW = 128_000

ROLES = ("planner", "executor", "summarizer", "classifier", "vision", "embedding")


@dataclass(frozen=True)
class ModelInfo:
    name: str
    roles: tuple[str, ...] = ("executor",)
    window: int = DEFAULT_CONTEXT_WINDOW
    thinking_off_ok: bool = True
    vision: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Matched by substring against the (lower-cased) model name, so provider-prefixed
# ids such as "zai/glm-5.3-fp8" resolve as well.
MODEL_REGISTRY: tuple[ModelInfo, ...] = (
    ModelInfo("qwen3.8-flash-next", ("executor", "summarizer", "vision"), 256_000, True, True),
    ModelInfo("deepseek-v4.1-flash", ("planner", "executor", "summarizer"), 1_000_000, True),
    ModelInfo("mimo-v2.6-pro", ("planner", "executor"), 1_000_000, True),
    ModelInfo("glm-5.3", ("planner", "executor"), 512_000, False),
    ModelInfo("clef", ("classifier",), 8_000, True),
)

# Kept for callers that import the old table.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {m.name: m.window for m in MODEL_REGISTRY if m.name != "clef"}


def model_info(model: str | None) -> ModelInfo | None:
    """Registry entry for `model`, or None when unknown."""
    name = (model or "").strip().lower()
    if not name:
        return None
    for info in MODEL_REGISTRY:
        if info.name in name:
            return info
    return None


def context_window_for(model: str | None) -> int:
    """Context window of `model`; 128K for unknown models."""
    info = model_info(model)
    return info.window if info else DEFAULT_CONTEXT_WINDOW


def thinking_off_ok(model: str | None) -> bool:
    """True unless the registry says `thinking: false` leaks reasoning for it."""
    info = model_info(model)
    return info.thinking_off_ok if info else True


def supports_vision(model: str | None) -> bool:
    info = model_info(model)
    return bool(info and info.vision)


def models_for_role(role: str) -> list[str]:
    """Registered model names that can serve `role`, in registry order."""
    return [m.name for m in MODEL_REGISTRY if role in m.roles]


def default_model_for(role: str, fallback: str = "") -> str:
    names = models_for_role(role)
    return names[0] if names else fallback
