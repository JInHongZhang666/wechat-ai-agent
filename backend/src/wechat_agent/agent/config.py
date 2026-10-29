"""Bounded configuration independent of the distribution model budget."""
import os

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    enabled: bool = False
    model: str | None = None
    max_steps: int = Field(default=6, ge=1, le=12)
    max_tool_calls: int = Field(default=8, ge=1, le=20)
    timeout_seconds: float = Field(default=120, ge=1, le=300)
    max_output_tokens: int = Field(default=1200, ge=128, le=4096)
    aihot_enabled: bool = True
    aihot_timeout_seconds: float = Field(default=8, ge=1, le=30)

    @classmethod
    def from_environment(cls):
        return cls(
            enabled=os.getenv('WECHAT_AGENT_AGENT_ENABLED', '0').lower() in {'1', 'true'},
            model=os.getenv('WECHAT_AGENT_AGENT_MODEL') or None,
            max_steps=os.getenv('WECHAT_AGENT_AGENT_MAX_STEPS', '6'),
            max_tool_calls=os.getenv('WECHAT_AGENT_AGENT_MAX_TOOL_CALLS', '8'),
            timeout_seconds=os.getenv('WECHAT_AGENT_AGENT_TIMEOUT_SECONDS', '120'),
            aihot_enabled=os.getenv('WECHAT_AGENT_AIHOT_ENABLED', '1').lower() in {'1', 'true'},
            aihot_timeout_seconds=os.getenv('WECHAT_AGENT_AIHOT_TIMEOUT_SECONDS', '8'),
        )
