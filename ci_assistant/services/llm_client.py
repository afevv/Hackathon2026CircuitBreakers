from __future__ import annotations

from ci_assistant.config import Settings
from ci_assistant.services.interfaces import LLMClient


class StubLLMClient:
    """Offline placeholder so the whole app runs without an API key."""

    def complete(self, system: str, prompt: str) -> str:
        return "[stub LLM] No model configured. Retrieved context:\n\n" + prompt[:600]


class AnthropicLLMClient:
    """Thin wrapper around the Anthropic SDK. Needs `pip install anthropic` and ANTHROPIC_API_KEY."""

    def __init__(self, model: str, max_tokens: int = 1024):
        import anthropic  # lazy: only required when this provider is selected

        self._client = anthropic.Anthropic()
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, system: str, prompt: str) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "anthropic":
        return AnthropicLLMClient(settings.llm_model)
    return StubLLMClient()
