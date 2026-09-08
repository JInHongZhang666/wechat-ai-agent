"""Deterministic Story event-layer primitives.

The package is intentionally provider-free: shadow clustering must not import
or call an LLM, embedding service, or remote API.
"""

from wechat_agent.story.models import STORY_STRATEGY_VERSION

__all__ = ["STORY_STRATEGY_VERSION"]
