from datetime import timedelta

from tests.story_factories import NOW, make_candidate_pair, make_story_article
from wechat_agent.story.clustering import (
    build_provisional_clusters,
    choose_primary_article,
    resolve_story_status,
)
from wechat_agent.story.models import PairSuggestion, StoryStatus


def test_transitive_edge_cannot_bridge_incompatible_versions():
    a = make_story_article(id="a", title="Qwen 3.5 正式发布")
    b = make_story_article(id="b", title="Qwen 新模型正式发布")
    c = make_story_article(id="c", title="Qwen 3.6 正式发布")
    pairs = (
        make_candidate_pair(a, b, score=0.91),
        make_candidate_pair(b, c, score=0.88),
    )

    clusters = build_provisional_clusters(
        [a, b, c], pairs, NOW, {"publisher:test": "publisher-test"}
    )

    assert sorted(len(cluster.article_ids) for cluster in clusters) == [1, 2]
    assert all(
        "different_explicit_version" in cluster.veto_reasons
        for cluster in clusters
    )


def test_primary_article_prefers_official_t1_over_earlier_coverage():
    coverage = make_story_article(
        id="coverage", tier="t2", published_at=NOW
    )
    official = make_story_article(
        id="official",
        tier="t1",
        source_type="official",
        published_at=NOW + timedelta(hours=1),
    )

    assert choose_primary_article([coverage, official]).id == "official"


def test_story_settles_at_48_hours_and_merged_status_wins():
    assert resolve_story_status(NOW - timedelta(hours=48), NOW) is StoryStatus.SETTLED
    assert (
        resolve_story_status(NOW - timedelta(hours=47, minutes=59), NOW)
        is StoryStatus.ACTIVE
    )
    assert (
        resolve_story_status(NOW - timedelta(minutes=1), NOW, merged=True)
        is StoryStatus.MERGED
    )


def test_ambiguous_edges_do_not_connect_and_every_article_is_emitted_once():
    left = make_story_article(id="left", title="Qwen 3.5 正式发布")
    right = make_story_article(id="right", title="Qwen 3.5 发布解读")
    unrelated = make_story_article(id="unrelated", title="OpenAI 发布 GPT X")
    ambiguous = make_candidate_pair(
        left,
        right,
        score=0.69,
        suggestion=PairSuggestion.AMBIGUOUS,
    )

    clusters = build_provisional_clusters(
        [left, right, unrelated],
        [ambiguous],
        NOW,
        {"publisher:test": "publisher-test"},
    )

    member_ids = [article_id for cluster in clusters for article_id in cluster.article_ids]
    assert sorted(member_ids) == ["left", "right", "unrelated"]
    assert [len(cluster.article_ids) for cluster in clusters] == [1, 1, 1]


def test_source_and_publisher_group_counts_use_registry_identity():
    official = make_story_article(
        id="official",
        source_id="source:official",
        source_identity="identity:official",
        title="Qwen 3.5 正式发布",
    )
    mirror = make_story_article(
        id="mirror",
        source_id="source:mirror",
        source_identity="identity:mirror",
        title="Qwen 3.5 正式发布",
    )

    cluster = build_provisional_clusters(
        [official, mirror],
        [make_candidate_pair(official, mirror, score=0.95)],
        NOW,
        {
            "source:official": "alibaba-group",
            "source:mirror": "alibaba-group",
        },
    )[0]

    assert cluster.source_count == 2
    assert cluster.publisher_group_count == 1
    assert cluster.signal_count == 1


def test_cluster_output_is_stable_when_inputs_are_reversed():
    a = make_story_article(id="a", title="Qwen 3.5 正式发布")
    b = make_story_article(id="b", title="Qwen 3.5 发布解读")
    c = make_story_article(id="c", title="OpenAI 发布 GPT X")
    pair = make_candidate_pair(a, b, score=0.90)
    groups = {"publisher:test": "publisher-test"}

    forward = build_provisional_clusters([a, b, c], [pair], NOW, groups)
    reverse = build_provisional_clusters([c, b, a], [pair], NOW, groups)

    assert forward == reverse
