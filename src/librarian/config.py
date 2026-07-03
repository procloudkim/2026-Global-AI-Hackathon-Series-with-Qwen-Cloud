"""Configuration: environment-driven, no secrets in code."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    light_model: str
    heavy_model: str


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
        heavy_model=os.environ.get("LIBRARIAN_HEAVY_MODEL", "qwen-plus"),
    )