"""Explicit, deterministic topic classification."""

from collections.abc import Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Literal

from wechat_agent.domain.models import TopicCategory


_KEYWORD_TABLE: tuple[tuple[TopicCategory, tuple[str, ...]], ...] = (
    (
        TopicCategory.TOOL,
        (
            "tool",
            "agent",
            "open-source",
            "open source",
            "sdk",
            "api",
            "release",
            "工具",
            "智能体",
            "开源",
        ),
    ),
    (
        TopicCategory.NEWS,
        (
            "news",
            "update",
            "regulation",
            "policy",
            "funding",
            "announcement",
            "新闻",
            "更新",
            "监管",
            "政策",
            "融资",
            "公告",
        ),
    ),
)


@dataclass(frozen=True)
class ClassificationResult:
    """A classification and the confidence metadata required by downstream records."""

    category: TopicCategory
    confidence: Literal["high", "low"]
    metadata: Mapping[str, str]

    def __post_init__(self) -> None:
        metadata = dict(self.metadata)
        metadata["classification_confidence"] = self.confidence
        object.__setattr__(self, "metadata", MappingProxyType(metadata))


def _keyword_matches(keyword: str, text: str) -> bool:
    if not keyword.isascii():
        return keyword in text
    return re.search(
        rf"(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])",
        text,
    ) is not None


def classify_with_confidence(title: str, summary: str) -> ClassificationResult:
    """Classify against the explicit keyword table, retaining confidence metadata."""
    text = f"{title}\n{summary}".casefold()
    matches: dict[TopicCategory, tuple[str, ...]] = {}
    for category, keywords in _KEYWORD_TABLE:
        category_matches = tuple(
            keyword for keyword in keywords if _keyword_matches(keyword, text)
        )
        if category_matches:
            matches[category] = category_matches

    if len(matches) > 1:
        return ClassificationResult(
            category=TopicCategory.NEWS,
            confidence="low",
            metadata={
                "classification_reason": "conflicting_categories",
                "matched_categories": ",".join(
                    category.value for category in matches
                ),
                **{
                    f"matched_{category.value}_keywords": ",".join(keywords)
                    for category, keywords in matches.items()
                },
            },
        )
    if matches:
        category = next(iter(matches))
        return ClassificationResult(
            category=category,
            confidence="high",
            metadata={},
        )
    return ClassificationResult(
        category=TopicCategory.NEWS,
        confidence="low",
        metadata={},
    )


def classify(title: str, summary: str) -> TopicCategory:
    """Compatibility wrapper returning only the legacy category value."""
    return classify_with_confidence(title, summary).category
