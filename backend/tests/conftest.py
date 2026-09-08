"""Hermetic defaults shared by the backend test suite."""

import pytest


@pytest.fixture(autouse=True)
def isolate_host_llm_credentials(monkeypatch):
    """Keep local provider credentials from changing deterministic tests."""
    for variable in (
        "WECHAT_AGENT_LLM_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)
