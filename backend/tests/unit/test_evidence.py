from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from wechat_agent.domain.models import (
    ArticleRecord,
    EvidenceFact,
    HeatConfidence,
    HeatScore,
    SourceType,
    TopicCandidate,
    TopicCategory,
)
from wechat_agent.evidence.build import build_evidence_pack


NOW = datetime(2026, 7, 25, 12, tzinfo=UTC)


def test_evidence_fact_requires_at_least_one_canonical_source_url():
    with pytest.raises(
        ValidationError, match="source_urls must contain at least one URL"
    ):
        EvidenceFact(claim="Unsupported claim")


def test_evidence_fact_rejects_conflicting_legacy_source_url():
    with pytest.raises(
        ValidationError, match="source_url must be included in source_urls"
    ):
        EvidenceFact(
            claim="Conflicting traceability",
            source_url="https://one.example/source",
            source_urls=["https://two.example/source"],
        )


def test_evidence_fact_deduplicates_canonical_source_urls():
    fact = EvidenceFact(
        claim="Traceable claim",
        source_urls=[
            "https://one.example/source",
            "https://one.example/source",
        ],
    )

    assert fact.source_urls == ["https://one.example/source"]


@pytest.fixture
def topic_candidate() -> TopicCandidate:
    return TopicCandidate(
        id="topic-1",
        title="Acme API update",
        category=TopicCategory.NEWS,
        heat_score=HeatScore(value=10, confidence=HeatConfidence.MEASURED),
        summary="A notable API update.",
        recommendation_reason="Recent and relevant.",
    )


@pytest.fixture
def related_articles() -> list[ArticleRecord]:
    return [
        _article(
            "official",
            "https://acme.example/announcement",
            "official",
            "Acme API supports 10 languages.",
            source_name="Acme",
            source_identity="acme",
            source_type_verified=True,
            source_verification_basis="collector verified acme.example ownership",
        ),
        _article(
            "report",
            "https://news.example/acme-api",
            "secondary",
            "Acme API supports 10 languages.",
            source_name="News Example",
            source_identity="news.example",
        ),
    ]


def test_every_fact_has_at_least_one_source(topic_candidate, related_articles):
    pack = build_evidence_pack(topic_candidate, related_articles)

    assert pack.facts
    assert all(fact.source_urls for fact in pack.facts)
    assert {url for fact in pack.facts for url in fact.source_urls} <= set(
        pack.source_urls
    )


def test_groups_identical_normalized_claims_and_allows_drafting_with_primary_source(
    topic_candidate, related_articles
):
    pack = build_evidence_pack(topic_candidate, related_articles)

    assert len(pack.facts) == 1
    assert pack.facts[0].source_urls == [
        "https://acme.example/announcement",
        "https://news.example/acme-api",
    ]
    assert pack.source_urls == [
        "https://acme.example/announcement",
        "https://news.example/acme-api",
    ]
    assert pack.ready_for_draft is True
    assert pack.unverified_claims == []
    assert "Acme API update" in pack.suggested_angle


def test_fact_maps_each_url_to_structured_source_metadata(
    topic_candidate, related_articles
):
    fact = build_evidence_pack(topic_candidate, related_articles).facts[0]

    assert set(fact.sources) == {
        "https://acme.example/announcement",
        "https://news.example/acme-api",
    }
    assert fact.sources["https://acme.example/announcement"].source_name == "Acme"
    assert fact.sources["https://acme.example/announcement"].source_identity == "acme"
    assert (
        fact.sources["https://acme.example/announcement"].source_type
        is SourceType.OFFICIAL
    )
    assert (
        fact.sources["https://acme.example/announcement"].provenance
        == "collector verified acme.example ownership"
    )
    assert (
        fact.sources["https://news.example/acme-api"].source_name == "News Example"
    )


def test_zero_fact_pack_excludes_all_input_urls_and_is_not_ready(topic_candidate):
    pack = build_evidence_pack(
        topic_candidate,
        [
            _article(
                "official",
                "https://acme.example/empty",
                "official",
                "   ",
                source_type_verified=True,
                source_verification_basis="verified domain",
            ),
            _article(
                "report",
                "https://news.example/empty",
                "secondary",
                "",
            ),
        ],
    )

    assert pack.facts == []
    assert pack.source_urls == []
    assert pack.conflicts == []
    assert pack.unverified_claims == []
    assert pack.ready_for_draft is False


def test_dangling_verified_official_does_not_verify_secondary_fact(topic_candidate):
    pack = build_evidence_pack(
        topic_candidate,
        [
            _article(
                "report",
                "https://news.example/acme-api",
                "secondary",
                "Acme API supports 10 languages.",
            ),
            _article(
                "official",
                "https://acme.example/empty",
                "official",
                "",
                source_type_verified=True,
                source_verification_basis="verified domain",
            ),
        ],
    )

    assert pack.source_urls == ["https://news.example/acme-api"]
    assert pack.unverified_claims == ["Acme API supports 10 languages."]
    assert pack.ready_for_draft is False


def test_multiple_urls_from_one_explicit_publisher_count_as_one_source(
    topic_candidate,
):
    claim = "Acme API supports 10 languages."
    pack = build_evidence_pack(
        topic_candidate,
        [
            _article(
                "announcement",
                "https://acme.example/announcement",
                "official",
                claim,
                source_identity="acme",
                source_type_verified=True,
                source_verification_basis="verified domain",
            ),
            _article(
                "documentation",
                "https://docs.acme.example/languages",
                "secondary",
                claim,
                source_identity="acme",
            ),
        ],
    )

    assert len(pack.facts[0].source_urls) == 2
    assert pack.unverified_claims == [claim]
    assert pack.ready_for_draft is False


def test_url_aliases_from_same_name_and_host_count_as_one_source(topic_candidate):
    claim = "Acme API supports 10 languages."
    pack = build_evidence_pack(
        topic_candidate,
        [
            _article(
                "canonical",
                "https://acme.example/announcement",
                "official",
                claim,
                source_name="Acme",
                source_type_verified=True,
                source_verification_basis="verified domain",
            ),
            _article(
                "alias",
                "https://acme.example/announcement?utm_source=test#details",
                "secondary",
                claim,
                source_name="  ACME  ",
            ),
        ],
    )

    assert len(pack.facts[0].source_urls) == 2
    assert pack.unverified_claims == [claim]
    assert pack.ready_for_draft is False


@pytest.mark.parametrize(
    ("source_type_verified", "source_verification_basis"),
    [(False, None), (True, "   ")],
)
def test_self_reported_official_type_does_not_unlock_verification(
    topic_candidate,
    source_type_verified,
    source_verification_basis,
):
    claim = "Acme API supports 10 languages."
    pack = build_evidence_pack(
        topic_candidate,
        [
            _article(
                "self-reported",
                "https://acme.example/announcement",
                "official",
                claim,
                source_identity="acme",
                source_type_verified=source_type_verified,
                source_verification_basis=source_verification_basis,
            ),
            _article(
                "report",
                "https://news.example/acme-api",
                "secondary",
                claim,
                source_identity="news.example",
            ),
        ],
    )

    assert pack.unverified_claims == [claim]
    assert pack.ready_for_draft is False


def test_single_verified_official_source_is_still_unverified(topic_candidate):
    claim = "Acme API supports 10 languages."
    pack = build_evidence_pack(
        topic_candidate,
        [
            _article(
                "official",
                "https://acme.example/announcement",
                "official",
                claim,
                source_identity="acme",
                source_type_verified=True,
                source_verification_basis="verified domain",
            )
        ],
    )

    assert pack.unverified_claims == [claim]
    assert pack.ready_for_draft is False


def test_records_different_values_for_the_same_claim_as_a_conflict(topic_candidate):
    pack = build_evidence_pack(
        topic_candidate,
        [
            _article(
                "official",
                "https://acme.example/announcement",
                "official",
                "Acme API supports 10 languages.",
            ),
            _article(
                "report",
                "https://news.example/acme-api",
                "secondary",
                "Acme API supports 12 languages.",
            ),
        ],
    )

    assert len(pack.conflicts) == 1
    assert pack.conflicts[0].subject == "acme api supports {value} languages"
    assert pack.conflicts[0].claims == [
        "Acme API supports 10 languages.",
        "Acme API supports 12 languages.",
    ]
    assert pack.conflicts[0].source_urls == [
        "https://acme.example/announcement",
        "https://news.example/acme-api",
    ]


def test_unresolved_conflict_marks_each_claim_unverified_and_blocks_ready(
    topic_candidate,
):
    claim_ten = "Acme API supports 10 languages."
    claim_twelve = "Acme API supports 12 languages."
    pack = build_evidence_pack(
        topic_candidate,
        [
            _article(
                "official-ten",
                "https://acme.example/ten",
                "official",
                claim_ten,
                source_identity="acme",
                source_type_verified=True,
                source_verification_basis="verified domain",
            ),
            _article(
                "report-ten",
                "https://news-one.example/ten",
                "secondary",
                claim_ten,
                source_identity="news-one",
            ),
            _article(
                "official-twelve",
                "https://acme.example/twelve",
                "official",
                claim_twelve,
                source_identity="acme",
                source_type_verified=True,
                source_verification_basis="verified domain",
            ),
            _article(
                "report-twelve",
                "https://news-two.example/twelve",
                "secondary",
                claim_twelve,
                source_identity="news-two",
            ),
        ],
    )

    assert len(pack.conflicts) == 1
    assert pack.unverified_claims == [claim_ten, claim_twelve]
    assert pack.ready_for_draft is False


def test_model_numbers_remain_part_of_subject_and_do_not_create_conflict(
    topic_candidate,
):
    pack = build_evidence_pack(
        topic_candidate,
        [
            _article(
                "official-model-3",
                "https://acme.example/model-3",
                "official",
                "Model 3 supports 10 languages.",
                source_identity="acme",
                source_type_verified=True,
                source_verification_basis="verified domain",
            ),
            _article(
                "report-model-3",
                "https://news-three.example/model-3",
                "secondary",
                "Model 3 supports 10 languages.",
                source_identity="news-three",
            ),
            _article(
                "official-model-4",
                "https://acme.example/model-4",
                "official",
                "Model 4 supports 10 languages.",
                source_identity="acme",
                source_type_verified=True,
                source_verification_basis="verified domain",
            ),
            _article(
                "report-model-4",
                "https://news-four.example/model-4",
                "secondary",
                "Model 4 supports 10 languages.",
                source_identity="news-four",
            ),
        ],
    )

    assert pack.conflicts == []
    assert pack.unverified_claims == []
    assert pack.ready_for_draft is True


def test_unparseable_claim_is_conservatively_left_unverified(topic_candidate):
    claim = "Acme launched yesterday."
    pack = build_evidence_pack(
        topic_candidate,
        [
            _article(
                "official",
                "https://acme.example/launch",
                "official",
                claim,
                source_identity="acme",
                source_type_verified=True,
                source_verification_basis="verified domain",
            ),
            _article(
                "report",
                "https://news.example/launch",
                "secondary",
                claim,
                source_identity="news.example",
            ),
        ],
    )

    assert pack.conflicts == []
    assert pack.unverified_claims == [claim]
    assert pack.ready_for_draft is False


def test_requires_two_distinct_sources_and_an_official_or_primary_source_for_draft(
    topic_candidate,
):
    pack = build_evidence_pack(
        topic_candidate,
        [
            _article(
                "report-one",
                "https://news.example/one",
                "secondary",
                "Acme API supports 10 languages.",
            ),
            _article(
                "report-two",
                "https://news.example/two",
                "secondary",
                "Acme API supports 10 languages.",
            ),
        ],
    )

    assert pack.ready_for_draft is False
    assert pack.unverified_claims == ["Acme API supports 10 languages."]


def _article(
    article_id: str,
    source_url: str,
    source_type: str,
    summary: str,
    *,
    source_name: str | None = None,
    source_identity: str | None = None,
    source_type_verified: bool = False,
    source_verification_basis: str | None = None,
) -> ArticleRecord:
    return ArticleRecord(
        id=article_id,
        title=article_id,
        source_name=source_name or article_id,
        source_url=source_url,
        source_type=source_type,
        source_identity=source_identity,
        source_type_verified=source_type_verified,
        source_verification_basis=source_verification_basis,
        published_at=NOW,
        collected_at=NOW,
        summary=summary,
        category=TopicCategory.NEWS,
    )
