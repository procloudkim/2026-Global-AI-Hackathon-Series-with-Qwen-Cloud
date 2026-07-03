"""Model Router: LIGHT for cheap tasks, HEAVY for judgment tasks.

All Qwen calls go through this module (single choke point for
token metering and Alibaba Cloud usage proof).
"""
from dataclasses import dataclass
from enum import Enum

from openai import OpenAI

from .config import get_settings


class Tier(str, Enum):
    LIGHT = "light"   # summarize / classify / extract links
    HEAVY = "heavy"   # conflict judgment / cross-update / final synthesis


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ModelRouter:
    def __init__(self) -> None:
        s = get_settings()
        self._client = OpenAI(api_key=s.api_key, base_url=s.base_url)
        self._models = {Tier.LIGHT: s.light_model, Tier.HEAVY: s.heavy_model}

    def chat(self, tier: Tier, system: str, user: str,
             temperature: float = 0.3) -> LLMResult:
        model = self._models[tier]
        resp = self._client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        usage = resp.usage
        return LLMResult(
            text=resp.choices[0].message.content or "",
            model=model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )


_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router