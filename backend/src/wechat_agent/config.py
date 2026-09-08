"""Application configuration boundary.

Runtime configuration is deferred to the application layer. LLM settings
default to DeepSeek via the OpenAI-compatible endpoint and can be
overridden through environment variables.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field


class LlmConfig(BaseModel):
    """LLM provider configuration with DeepSeek defaults.

    Environment variables
    ---------------------
    WECHAT_AGENT_LLM_API_KEY
        Provider-neutral credential for an OpenAI-compatible endpoint.
        ``DASHSCOPE_API_KEY`` and the legacy ``DEEPSEEK_API_KEY`` remain
        supported fallbacks. When all are unset, the system uses rules.
    WECHAT_AGENT_LLM_BASE_URL
        Override the OpenAI-compatible base URL (defaults to DeepSeek).
    WECHAT_AGENT_LLM_MODEL
        Override the model name (defaults to ``deepseek-chat``).
    """

    api_key: str = Field(
        default_factory=lambda: (
            os.getenv("WECHAT_AGENT_LLM_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY", "")
        )
    )
    base_url: str = Field(
        default_factory=lambda: os.getenv(
            "WECHAT_AGENT_LLM_BASE_URL",
            "https://api.deepseek.com/v1",
        )
    )
    model: str = Field(
        default_factory=lambda: os.getenv(
            "WECHAT_AGENT_LLM_MODEL", "deepseek-chat"
        )
    )
    pre_screen_model: str = Field(
        default_factory=lambda: os.getenv(
            "WECHAT_AGENT_PRE_SCREEN_MODEL", "deepseek-chat"
        )
    )
    scoring_model: str = Field(
        default_factory=lambda: os.getenv(
            "WECHAT_AGENT_SCORING_MODEL", "deepseek-chat"
        )
    )
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=256, le=16384)
    request_timeout: float = Field(default=60.0, ge=10, le=300)
    max_retries: int = Field(
        default_factory=lambda: int(os.getenv("WECHAT_AGENT_LLM_MAX_RETRIES", "1")),
        ge=0,
        le=3,
    )
    distribution_daily_call_limit: int = Field(
        default_factory=lambda: int(
            os.getenv("WECHAT_AGENT_DISTRIBUTION_LLM_DAILY_CALL_LIMIT", "300")
        ),
        ge=0,
        le=100_000,
    )
    input_cost_per_million: float = Field(
        default_factory=lambda: float(os.getenv("WECHAT_AGENT_LLM_INPUT_COST_PER_MILLION", "0")),
        ge=0,
    )
    output_cost_per_million: float = Field(
        default_factory=lambda: float(os.getenv("WECHAT_AGENT_LLM_OUTPUT_COST_PER_MILLION", "0")),
        ge=0,
    )

    @property
    def is_available(self) -> bool:
        return bool(self.api_key.strip())


def load_llm_config() -> LlmConfig | None:
    """Return an ``LlmConfig`` when a key is present, otherwise ``None``."""
    config = LlmConfig()
    return config if config.is_available else None
