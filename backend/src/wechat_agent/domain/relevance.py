"""Deterministic relevance guard for broad, mixed-topic source feeds."""

from __future__ import annotations

import re

from wechat_agent.domain.models import ArticleRecord


AI_RELEVANCE_KEYWORDS = (
    "ai", "人工智能", "大模型", "智能体", "agent", "llm", "gpt",
    "claude", "gemini", "deepseek", "机器学习", "生成式", "aigc",
    "openai", "anthropic", "hugging face", "copilot", "prompt", "mcp",
)

_MIXED_TOPIC_SOURCES = {
    "publisher:ithome",
    "publisher:sspai",
}


def contains_relevance_keyword(
    text: str,
    keywords: tuple[str, ...] = AI_RELEVANCE_KEYWORDS,
) -> bool:
    normalized = text.casefold()
    for raw_keyword in keywords:
        keyword = raw_keyword.casefold()
        if keyword.isascii() and keyword.replace("-", "").isalnum():
            if re.search(
                rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])",
                normalized,
            ):
                return True
        elif keyword in normalized:
            return True
    return False


def is_distribution_eligible(article: ArticleRecord) -> bool:
    """Require an explicit AI signal only for known mixed-topic feeds."""
    if article.source_identity not in _MIXED_TOPIC_SOURCES:
        return True
    # Mixed feeds often append unrelated recommendations/footer text to the
    # description. Requiring the signal in the headline avoids those false
    # positives while category-specific feeds remain trusted as a whole.
    return contains_relevance_keyword(article.title)
