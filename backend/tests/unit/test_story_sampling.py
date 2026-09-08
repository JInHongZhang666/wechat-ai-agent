from dataclasses import dataclass

from tests.story_factories import make_candidate_pair, make_story_article
from wechat_agent.domain.models import TopicCategory
from wechat_agent.story.models import PairSuggestion
from wechat_agent.story.sampling import (
    select_annotation_candidates,
    stratum_counts,
)


CATEGORIES = (
    TopicCategory.AI_MODELS,
    TopicCategory.AI_PRODUCTS,
    TopicCategory.INDUSTRY,
    TopicCategory.PAPER,
    TopicCategory.TIP,
)


@dataclass(frozen=True)
class PairFixture:
    pairs: tuple
    articles: tuple


def _pair_fixture() -> PairFixture:
    pairs = []
    articles = []
    for index in range(60):
        category = CATEGORIES[index % len(CATEGORIES)]
        left = make_story_article(
            id=f"left-{index:02d}",
            source_id=f"publisher:left-{index:02d}",
            source_identity=f"publisher:left-{index:02d}",
            source_name=f"Left Publisher {index:02d}",
            title=f"AI event report {index:02d}",
            summary="A" * 700,
            category=category,
        )
        right = make_story_article(
            id=f"right-{index:02d}",
            source_id=f"publisher:right-{index:02d}",
            source_identity=f"publisher:right-{index:02d}",
            source_name=f"Right Publisher {index:02d}",
            title=f"AI event coverage {index:02d}",
            category=category,
        )
        if index < 20:
            pair = make_candidate_pair(
                left,
                right,
                score=0.50 + index / 1000,
                suggestion=PairSuggestion.AMBIGUOUS,
            )
        elif index < 40:
            pair = make_candidate_pair(
                left,
                right,
                score=0.65,
                suggestion=PairSuggestion.DIFFERENT,
                veto_reason="different_explicit_version",
            ).model_copy(
                update={"features": {"title_jaccard": 0.5, "product_overlap": 1.0}}
            )
        elif index < 50:
            pair = make_candidate_pair(
                left,
                right,
                score=0.85,
                suggestion=PairSuggestion.SAME,
            )
        else:
            pair = make_candidate_pair(
                left,
                right,
                score=0.10,
                suggestion=PairSuggestion.DIFFERENT,
            )
        articles.extend((left, right))
        pairs.append(pair)
    return PairFixture(tuple(pairs), tuple(articles))


def test_export_candidates_hide_algorithm_answer_and_cap_summaries():
    fixture = _pair_fixture()

    rows = select_annotation_candidates(
        fixture.pairs,
        fixture.articles,
        limit=50,
    )
    payload = rows[0].model_dump(mode="json", by_alias=True)

    assert payload.keys() >= {"pairId", "articleA", "articleB"}
    assert {
        "score",
        "suggestion",
        "vetoReason",
        "clusterId",
        "systemAnswer",
        "samplingStratum",
    }.isdisjoint(payload)
    assert len(payload["articleA"]["summary"]) <= 500


def test_sampling_prioritizes_ambiguous_and_hard_negative_strata():
    fixture = _pair_fixture()

    rows = select_annotation_candidates(
        fixture.pairs,
        fixture.articles,
        limit=50,
    )

    counts = stratum_counts(rows)
    assert len(rows) == 50
    assert counts["ambiguous"] >= 15
    assert counts["hard_negative"] >= 15
    assert counts["multi_publisher_positive"] >= 10


def test_sampling_is_deterministic_and_deduplicated_when_inputs_reverse():
    fixture = _pair_fixture()

    forward = select_annotation_candidates(
        fixture.pairs,
        fixture.articles,
        limit=50,
    )
    reverse = select_annotation_candidates(
        reversed(fixture.pairs),
        reversed(fixture.articles),
        limit=50,
    )

    assert forward == reverse
    assert len({row.pair_id for row in forward}) == len(forward)
