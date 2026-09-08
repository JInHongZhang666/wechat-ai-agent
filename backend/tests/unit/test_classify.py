import pytest

from wechat_agent.domain.models import TopicCategory
from wechat_agent.topics.classify import (
    ClassificationResult,
    classify,
    classify_with_confidence,
)


def test_classify_uses_explicit_tool_keywords():
    assert (
        classify("New open-source AI tool released", "A developer tool for agents")
        is TopicCategory.TOOL
    )


def test_classify_with_confidence_marks_keyword_match_high():
    result = classify_with_confidence(
        "New open-source AI tool released", "A developer tool for agents"
    )

    assert result.category is TopicCategory.TOOL
    assert result.confidence == "high"
    assert result.metadata["classification_confidence"] == "high"


def test_classify_with_confidence_defaults_ambiguous_content_to_low_confidence_news():
    result = classify_with_confidence("AI overview", "A short general discussion")

    assert result.category is TopicCategory.NEWS
    assert result.confidence == "low"
    assert result.metadata["classification_confidence"] == "low"


def test_classify_with_confidence_marks_cross_category_matches_as_a_conflict():
    result = classify_with_confidence(
        "Agent regulation update",
        "Policy announcement",
    )

    assert result.category is TopicCategory.NEWS
    assert result.confidence == "low"
    assert result.metadata["classification_confidence"] == "low"
    assert result.metadata["classification_reason"] == "conflicting_categories"
    assert result.metadata["matched_categories"] == "tool,news"
    assert result.metadata["matched_tool_keywords"] == "agent"
    assert result.metadata["matched_news_keywords"] == (
        "update,regulation,policy,announcement"
    )


def test_english_keywords_match_whole_words_not_substrings():
    result = classify_with_confidence(
        "Capital funding news",
        "A financing round for the company",
    )

    assert result.category is TopicCategory.NEWS
    assert result.confidence == "high"


def test_chinese_keywords_match_as_continuous_terms():
    result = classify_with_confidence(
        "这款开源工具箱很实用",
        "面向开发者的效率产品",
    )

    assert result.category is TopicCategory.TOOL
    assert result.confidence == "high"


def test_classification_metadata_is_immutable_and_derived_from_confidence():
    result = ClassificationResult(
        category=TopicCategory.TOOL,
        confidence="high",
        metadata={"classification_confidence": "low"},
    )

    assert result.metadata["classification_confidence"] == "high"
    with pytest.raises(TypeError):
        result.metadata["classification_confidence"] = "low"


def test_classify_remains_a_compatibility_wrapper():
    assert classify("AI overview", "A short general discussion") is TopicCategory.NEWS
