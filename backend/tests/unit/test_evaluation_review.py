from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from wechat_agent.domain.models import ArticleRecord, TopicCategory
from wechat_agent.evaluation.models import (
    GoldLabel,
    GoldSample,
    LabelingCandidate,
    PredictionSnapshot,
    SelectionReviewDecision,
    SelectionReviewItem,
    WorkbenchItem,
)
from wechat_agent.evaluation.review import (
    build_selection_audit_report,
    select_selection_review_items,
)


NOW = datetime(2026, 8, 3, tzinfo=UTC)


def _item(
    position: int,
    category: TopicCategory,
    *,
    relevant: bool = True,
    selected: bool = True,
    annotation_method: str = "human",
    tier: str = "t2",
) -> WorkbenchItem:
    article_id = f"review-{position}-{category.value}"
    article = ArticleRecord(
        id=article_id,
        source_id=f"source:{article_id}",
        title=f"Review sample {position}",
        source_name="Review Source",
        source_url=f"https://review-source.ai/{article_id}",
        published_at=NOW,
        collected_at=NOW,
        summary="A complete review fixture.",
        category=category,
        source_identity=f"source:{article_id}",
        tier=tier,
    )
    label = GoldLabel(
        relevant=relevant,
        category=category,
        selected=selected,
        primary_source_correct=True,
        story_key=None,
        creation_worthy=selected,
        evidence_sufficient=True,
        annotation_method=annotation_method,
        data_license="public-source-metadata-only",
        annotator="reviewer-a",
        annotated_at=NOW,
    )
    return WorkbenchItem(
        position=position,
        candidate=LabelingCandidate(
            evaluation_group="selection-review-fixture",
            article=article,
            suggestion=PredictionSnapshot(
                relevant=True,
                category=category,
                selected=True,
            ),
        ),
        label=label,
    )


def test_selection_review_reason_must_match_decision_direction():
    with pytest.raises(ValidationError):
        SelectionReviewDecision(
            selected=False,
            reason_code="high_information_gain",
            notes="",
            reviewer="reviewer-b",
            reviewed_at=NOW,
            annotation_method="human",
        )


def test_selection_review_pool_is_stable_and_uses_only_human_relevant_selected():
    eligible = [
        _item(1, TopicCategory.PAPER),
        _item(2, TopicCategory.TIP),
        _item(3, TopicCategory.INDUSTRY),
        _item(4, TopicCategory.AI_PRODUCTS),
        _item(5, TopicCategory.AI_MODELS),
        _item(6, TopicCategory.INDUSTRY, tier="t1"),
    ]
    items = eligible + [
        _item(7, TopicCategory.PAPER, selected=False),
        _item(8, TopicCategory.TIP, relevant=False, selected=False),
        _item(9, TopicCategory.INDUSTRY, annotation_method="synthetic"),
    ]

    first = select_selection_review_items(items, target_count=6)
    second = select_selection_review_items(list(reversed(items)), target_count=6)

    assert [item.candidate.article.id for item in first] == [
        item.candidate.article.id for item in second
    ]
    assert len(first) == 6
    assert all(item.label is not None for item in first)
    assert all(item.label.annotation_method == "human" for item in first if item.label)
    assert all(item.label.relevant and item.label.selected for item in first if item.label)
    assert {item.label.category for item in first if item.label} >= {
        TopicCategory.PAPER,
        TopicCategory.TIP,
    }


def test_selection_audit_report_contains_change_rate_reasons_and_metrics():
    first = _item(1, TopicCategory.PAPER)
    second = _item(2, TopicCategory.TIP)
    remove = SelectionReviewDecision(
        selected=False,
        reason_code="low_information_gain",
        reviewer="reviewer-b",
        reviewed_at=NOW,
    )
    keep = SelectionReviewDecision(
        selected=True,
        reason_code="wechat_value",
        reviewer="reviewer-b",
        reviewed_at=NOW,
    )
    reviews = [
        SelectionReviewItem(
            position=1,
            reason="human-relevant-selected",
            candidate=first.candidate,
            initial_label=first.label,
            review=remove,
        ),
        SelectionReviewItem(
            position=2,
            reason="human-relevant-selected",
            candidate=second.candidate,
            initial_label=second.label,
            review=keep,
        ),
    ]
    before_samples = [
        GoldSample(
            evaluation_group=item.candidate.evaluation_group,
            article=item.candidate.article,
            label=item.label,
        )
        for item in (first, second)
    ]
    after_samples = [
        before_samples[0].model_copy(
            update={
                "label": before_samples[0].label.model_copy(
                    update={"selected": False}
                )
            }
        ),
        before_samples[1],
    ]

    report = build_selection_audit_report(
        review_set_id="selection-audit-v1",
        reviews=reviews,
        before_samples=before_samples,
        after_samples=after_samples,
    )

    assert report["status"] == "complete"
    assert report["changedCount"] == 1
    assert report["changeRate"] == 0.5
    assert report["reasonCounts"] == {
        "low_information_gain": 1,
        "wechat_value": 1,
    }
    assert report["beforeMetrics"]["sampleCount"] == 2
    assert report["afterMetrics"]["sampleCount"] == 2
