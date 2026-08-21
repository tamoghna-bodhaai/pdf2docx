"""Model-selection policy for remote vision requests."""

from __future__ import annotations


DEFAULT_MODEL = "google/gemini-3.6-flash"


class ModelPolicyError(ValueError):
    """Raised before a disallowed model can reach the remote API."""


def model_policy_error(model: str) -> str | None:
    """Explain why ``model`` cannot be used, or return ``None`` when allowed.

    OpenRouter's own aliases are excluded as well as explicit Claude model IDs:
    an alias such as ``openrouter/auto`` can select Claude behind the scenes and
    therefore cannot provide the no-Claude guarantee this application requires.
    """
    candidate = (model or "").strip()
    if not candidate:
        return "A model id is required."

    normalised = candidate.casefold()
    provider = normalised.partition("/")[0]
    if provider == "anthropic" or "claude" in normalised:
        return "Claude models are not supported. Choose an explicit non-Anthropic model id."
    if provider == "openrouter":
        return (
            "Dynamic OpenRouter model aliases are not supported because they may route "
            "requests to Claude. Choose an explicit provider/model id."
        )
    return None


def is_model_allowed(model: str) -> bool:
    """Whether a model is safe to expose in the picker or use for a request."""
    return model_policy_error(model) is None


def require_model_allowed(model: str) -> str:
    """Return a normalised allowed model id, failing before any remote call."""
    candidate = (model or "").strip()
    if reason := model_policy_error(candidate):
        raise ModelPolicyError(reason)
    return candidate
