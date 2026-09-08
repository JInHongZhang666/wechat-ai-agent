from datetime import timedelta

import pytest

from tests.story_factories import NOW, make_story_article
from wechat_agent.story.candidates import (
    DEFAULT_POLICY,
    recall_candidate_pairs,
    score_candidate_pair,
)
from wechat_agent.story.models import PairSuggestion
from wechat_agent.story.normalize import normalize_article


def _score(left, right):
    return score_candidate_pair(
        normalize_article(left), normalize_article(right), NOW, DEFAULT_POLICY
    )


def test_official_and_coverage_are_recalled():
    official = make_story_article(
        id="official",
        title="Qwen 3.5-35B 正式发布",
        source_identity="official:qwen",
    )
    coverage = make_story_article(
        id="coverage",
        title="阿里发布 Qwen 3.5 35B 模型",
        source_identity="publisher:coverage",
        published_at=official.published_at + timedelta(hours=2),
    )

    pairs = recall_candidate_pairs([official], [official, coverage], NOW)

    assert len(pairs) == 1
    assert pairs[0].suggestion in {PairSuggestion.SAME, PairSuggestion.AMBIGUOUS}
    assert pairs[0].veto_reason is None


def test_exact_normalized_title_without_veto_is_same_event():
    official = make_story_article(
        id="exact-official",
        title="Qwen 3.5 正式发布",
        source_identity="official:qwen",
    )
    coverage = make_story_article(
        id="exact-coverage",
        title="Qwen 3.5 正式发布",
        source_identity="publisher:coverage",
        published_at=official.published_at + timedelta(hours=2),
    )

    pair = _score(official, coverage)

    assert pair.suggestion is PairSuggestion.SAME
    assert pair.features["exact_title_match"] == 1.0
    assert pair.veto_reason is None


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            make_story_article(
                id="url-a", source_url="https://example.com/item?utm_source=a"
            ),
            make_story_article(
                id="url-b", source_url="https://example.com/item?utm_source=b"
            ),
        ),
        (
            make_story_article(id="paper-a", title="论文 2408.12345 提出新方法"),
            make_story_article(id="paper-b", title="解读 arXiv 2408.12345"),
        ),
        (
            make_story_article(
                id="repo-a",
                title="Acme Agent 正式开源",
                source_url="https://github.com/acme/agent",
            ),
            make_story_article(
                id="repo-b",
                title="Acme Agent 仓库发布",
                source_url="https://github.com/acme/agent?ref=feed",
            ),
        ),
    ],
)
def test_strong_duplicate_anchors_are_recalled(left, right):
    pairs = recall_candidate_pairs([left], [left, right], NOW)

    assert len(pairs) == 1
    assert pairs[0].suggestion in {PairSuggestion.SAME, PairSuggestion.AMBIGUOUS}
    assert pairs[0].veto_reason is None


def test_different_explicit_versions_are_hard_vetoed():
    pair = _score(
        make_story_article(id="v35", title="Qwen 3.5 正式发布"),
        make_story_article(id="v36", title="Qwen 3.6 正式发布"),
    )

    assert pair.suggestion is PairSuggestion.DIFFERENT
    assert pair.veto_reason == "different_explicit_version"


@pytest.mark.parametrize(
    ("left", "right", "reason"),
    [
        (
            make_story_article(id="id-a", title="OpenAI 论文 2408.11111"),
            make_story_article(id="id-b", title="OpenAI 论文 2408.22222"),
            "different_identifier",
        ),
        (
            make_story_article(id="action-a", title="OpenAI 发布新平台"),
            make_story_article(id="action-b", title="OpenAI 收购 Acme"),
            "same_org_different_action",
        ),
        (
            make_story_article(id="actor-a", title="OpenAI 发布 AI 大模型"),
            make_story_article(id="actor-b", title="Anthropic 发布 AI 大模型"),
            "same_topic_different_actor",
        ),
        (
            make_story_article(id="rumor", title="Qwen 4 或将发布"),
            make_story_article(id="occurred", title="Qwen 4 正式发布"),
            "prediction_vs_occurrence",
        ),
        (
            make_story_article(id="overview", title="Qwen 4 年度盘点与教程"),
            make_story_article(id="event", title="Qwen 4 正式发布"),
            "overview_vs_specific_event",
        ),
        (
            make_story_article(id="shanghai", title="OpenAI 上海大会发布 GPT X"),
            make_story_article(id="new-york", title="OpenAI 纽约大会发布 GPT X"),
            "different_time_or_place",
        ),
        (
            make_story_article(id="release", title="OpenAI 发布 GPT 6"),
            make_story_article(id="followup", title="GPT 6 因合规问题被下架"),
            "followup_new_event",
        ),
    ],
)
def test_hard_negative_reason_families_are_explainable(left, right, reason):
    pair = _score(left, right)

    assert pair.suggestion is PairSuggestion.DIFFERENT
    assert pair.veto_reason == reason


def test_pair_at_24_hour_boundary_is_recalled_but_later_pair_is_not():
    left = make_story_article(id="left", title="OpenAI 发布 GPT X")
    boundary = make_story_article(
        id="boundary",
        title="OpenAI 正式发布 GPT X",
        published_at=left.published_at + timedelta(hours=24),
    )
    outside = make_story_article(
        id="outside",
        title="OpenAI 正式发布 GPT X",
        published_at=left.published_at + timedelta(hours=24, seconds=1),
    )

    pairs = recall_candidate_pairs([left], [left, boundary, outside], NOW)

    assert [(pair.article_a_id, pair.article_b_id) for pair in pairs] == [
        ("boundary", "left")
    ]


def test_recall_deduplicates_changed_pairs_and_sorts_by_score_then_key():
    anchor = make_story_article(id="anchor", title="Qwen 3.5 正式发布")
    exact = make_story_article(
        id="exact",
        title="Qwen 3.5 正式发布",
        source_identity="publisher:exact",
    )
    coverage = make_story_article(
        id="coverage",
        title="阿里发布 Qwen 3.5 模型",
        source_identity="publisher:coverage",
    )

    pairs = recall_candidate_pairs(
        [anchor, exact, coverage], [anchor, exact, coverage], NOW
    )

    assert len({pair.pair_key for pair in pairs}) == len(pairs)
    assert pairs == tuple(sorted(pairs, key=lambda pair: (-pair.score, pair.pair_key)))


def test_unrelated_low_signal_articles_are_not_recalled():
    left = make_story_article(id="left", title="OpenAI 发布 GPT X")
    right = make_story_article(id="right", title="深圳天气转晴并开放新公园")

    assert recall_candidate_pairs([left], [left, right], NOW) == ()
