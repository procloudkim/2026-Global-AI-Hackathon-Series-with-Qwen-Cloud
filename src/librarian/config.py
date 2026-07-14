"""Configuration: environment-driven, no secrets in code."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    light_model: str
    heavy_model: str
    request_timeout_seconds: float
    max_retries: int
    max_completion_tokens: int


def _bounded_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def get_memory_root() -> Path:
    """Return the persistent store root without requiring provider credentials."""
    value = os.environ.get("LIBRARIAN_MEMORY_ROOT", "memory").strip()
    if not value:
        raise RuntimeError("LIBRARIAN_MEMORY_ROOT must be non-empty")
    return Path(value)


def get_deployed_sha() -> str:
    value = os.environ.get("LIBRARIAN_DEPLOYED_SHA", "unknown").strip()
    return value or "unknown"


def get_qwen_health_token() -> str:
    return os.environ.get("LIBRARIAN_QWEN_HEALTH_TOKEN", "").strip()


def get_rate_limit_per_minute() -> int:
    return _bounded_int(
        "LIBRARIAN_RATE_LIMIT_PER_MINUTE", 60, minimum=1, maximum=600
    )


def get_settings() -> Settings:
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key or api_key.startswith("sk-your"):
        raise RuntimeError("DASHSCOPE_API_KEY is not configured (.env)")
    return Settings(
        api_key=api_key,
        base_url=os.environ.get(
            "DASHSCOPE_BASE_URL",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        ),
        light_model=os.environ.get("LIBRARIAN_LIGHT_MODEL", "qwen-flash"),
        heavy_model=os.environ.get(
            "LIBRARIAN_HEAVY_MODEL", "qwen-plus-2025-07-28"
        ),
        request_timeout_seconds=_bounded_float(
            "LIBRARIAN_QWEN_TIMEOUT_SECONDS", 30.0, minimum=1.0, maximum=120.0
        ),
        max_retries=_bounded_int(
            "LIBRARIAN_QWEN_MAX_RETRIES", 0, minimum=0, maximum=2
        ),
        max_completion_tokens=_bounded_int(
            "LIBRARIAN_QWEN_MAX_COMPLETION_TOKENS",
            1600,
            minimum=8,
            maximum=4096,
        ),
    )
