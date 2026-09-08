from datetime import UTC, datetime
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from wechat_agent.domain.models import (
    ArticleRecord,
    HeatConfidence,
    HeatScore,
    SourceType,
    TopicCategory,
)
from wechat_agent.evaluation.__main__ import export_candidates, main
from wechat_agent.evaluation.io import read_gold
from wechat_agent.evaluation.models import GoldLabel, GoldSample, LabelingCandidate
from wechat_agent.evaluation.replay import replay_samples
from wechat_agent.scoring.curation import (
    DEFAULT_CURATED_PER_CATEGORY,
    DEFAULT_CURATED_PAPERS,
    get_curation_priority,
    get_quality_adjustment,
    get_threshold_for,
    is_curated,
    select_curated,
)
from wechat_agent.evaluation.workbench import is_evaluation_eligible_article
from wechat_agent.services.pipeline import (
    ContentPipeline,
    DISTRIBUTION_STRATEGY_VERSION,
)
from wechat_agent.storage.sqlite import (
    SqliteArticleRepository,
    SqliteCurationDecisionRepository,
)


NOW = datetime(2026, 8, 2, 2, tzinfo=UTC)


def _article(identity: str, *, title: str, tier: str, category: TopicCategory):
    return ArticleRecord(
        id=identity,
        source_id=f"source:{identity}",
        title=title,
        source_name="Evaluation Source",
        source_url=f"https://example.test/{identity}",
        published_at=NOW,
        collected_at=NOW,
        summary=title,
        category=category,
        source_identity=f"source:{identity}",
        tier=tier,
    )


def _label(*, relevant: bool, category: TopicCategory, selected: bool):
    return GoldLabel(
        relevant=relevant,
        category=category,
        selected=selected,
        primary_source_correct=True,
        story_key=None,
        creation_worthy=selected,
        evidence_sufficient=selected,
        annotation_method="human",
        data_license="public-source-metadata-only",
        annotator="human-test",
        annotated_at=NOW,
    )


def test_gold_label_rejects_partial_or_self_contradictory_truth():
    with pytest.raises(ValidationError):
        GoldLabel(
            relevant=False,
            category=TopicCategory.AI_MODELS,
            selected=True,
            primary_source_correct=False,
            story_key=None,
            creation_worthy=False,
            evidence_sufficient=False,
            annotation_method="human",
            data_license="public-source-metadata-only",
            annotator="human-test",
            annotated_at=NOW,
        )


def test_gold_label_requires_annotation_provenance():
    with pytest.raises(ValidationError):
        GoldLabel(
            relevant=True,
            category=TopicCategory.AI_MODELS,
            selected=True,
            primary_source_correct=True,
            story_key="missing-provenance",
            creation_worthy=True,
            evidence_sufficient=True,
            annotator="human-test",
            annotated_at=NOW,
        )


def test_replay_reports_deterministic_metrics_and_mismatches():
    positive = _article(
        "positive",
        title="Example AI model official release",
        tier="t1",
        category=TopicCategory.AI_MODELS,
    )
    negative = _article(
        "negative",
        title="Unrelated phone sale",
        tier="t2",
        category=TopicCategory.INDUSTRY,
    )
    samples = [
        GoldSample(
            evaluation_group="batch-1",
            article=positive,
            label=_label(
                relevant=True,
                category=TopicCategory.AI_MODELS,
                selected=True,
            ),
        ),
        GoldSample(
            evaluation_group="batch-1",
            article=negative,
            label=_label(
                relevant=False,
                category=TopicCategory.INDUSTRY,
                selected=False,
            ),
        ),
    ]

    report = replay_samples(samples)

    assert report["sampleCount"] == 2
    assert report["strategyVersion"] == DISTRIBUTION_STRATEGY_VERSION
    assert report["metrics"]["selection"]["precision"] == 0.5
    assert report["metrics"]["relevance"]["precision"] == 0.5
    assert report["mismatchCount"] == 1
    assert report["mismatches"][0]["articleId"] == "negative"


def test_curation_v3_2_recovers_t2_industry_items_without_unbounded_slots():
    articles = [
        _article(
            f"industry-{index}",
            title=f"AI industry signal {index}",
            tier="t2",
            category=TopicCategory.INDUSTRY,
        )
        for index in range(DEFAULT_CURATED_PER_CATEGORY + 1)
    ]
    scores = {
        article.id: HeatScore(
            value=58 + index,
            confidence=HeatConfidence.ESTIMATED,
            components={},
        )
        for index, article in enumerate(articles)
    }

    assert get_threshold_for(articles[0]) == 58.0
    assert is_curated(articles[0], scores[articles[0].id]) is True
    assert [
        article.id
        for article in select_curated(articles, scores)
    ] == [
        f"industry-{index}"
        for index in range(
            DEFAULT_CURATED_PER_CATEGORY,
            0,
            -1,
        )
    ]


def test_curation_v3_2_applies_explainable_quality_signals():
    culture = _article(
        "culture",
        title="Singer releases controversial AI song",
        tier="t2",
        category=TopicCategory.INDUSTRY,
    )
    official = _article(
        "official",
        title="Launching ChatGPT agent security research",
        tier="t2",
        category=TopicCategory.INDUSTRY,
    ).model_copy(update={"source_type": SourceType.OFFICIAL})
    thin_release = _article(
        "thin-release",
        title="condense-json 1.0",
        tier="t2",
        category=TopicCategory.TIP,
    )
    culture_score = HeatScore(
        value=60,
        confidence=HeatConfidence.ESTIMATED,
    )
    official_score = HeatScore(
        value=57,
        confidence=HeatConfidence.ESTIMATED,
    )
    thin_release_score = HeatScore(
        value=61,
        confidence=HeatConfidence.ESTIMATED,
    )

    assert get_quality_adjustment(culture) == -5.0
    assert get_quality_adjustment(official) == 1.5
    assert get_quality_adjustment(thin_release) == -6.0
    assert get_curation_priority(culture, culture_score) == 55.0
    assert is_curated(culture, culture_score) is False
    assert is_curated(official, official_score) is True
    assert is_curated(thin_release, thin_release_score) is False


def test_curation_v3_2_limits_default_paper_mix_to_three_items():
    articles = [
        _article(
            f"paper-{index}",
            title=f"AI inference framework paper {index}",
            tier="t1",
            category=TopicCategory.PAPER,
        )
        for index in range(5)
    ]
    scores = {
        article.id: HeatScore(
            value=70 + index,
            confidence=HeatConfidence.ESTIMATED,
        )
        for index, article in enumerate(articles)
    }

    assert len(select_curated(articles, scores)) == DEFAULT_CURATED_PAPERS
    assert len(select_curated(articles, scores, per_category=4)) == 4


def test_distribution_v3_2_meets_reviewed_gold_selection_floor():
    gold_path = (
        Path(__file__).resolve().parents[2]
        / "evaluation"
        / "gold"
        / "news-gold-v1.2.jsonl"
    )
    samples = list(read_gold(gold_path))

    metrics = replay_samples(samples)["metrics"]["selection"]

    assert metrics["precision"] >= 0.70
    assert metrics["recall"] >= 0.54
    assert metrics["f1"] >= 0.62


def test_candidate_export_keeps_suggestions_separate_from_gold(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'evaluation.db'}"
    article_repo = SqliteArticleRepository(database_url)
    decision_repo = SqliteCurationDecisionRepository(article_repo.engine)
    pipeline = ContentPipeline(
        article_repo,
        curation_repository=decision_repo,
        enable_pre_screen=False,
    )
    article = _article(
        "candidate",
        title="Example AI product launch",
        tier="t1",
        category=TopicCategory.AI_PRODUCTS,
    )
    pipeline.run([article], NOW)
    output = tmp_path / "candidates.jsonl"

    assert export_candidates(database_url, output, 200) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    candidate = LabelingCandidate.model_validate(payload)

    assert candidate.article.id == article.id
    assert candidate.suggestion.strategy_version == DISTRIBUTION_STRATEGY_VERSION
    assert "label" not in payload


def test_human_pool_rejects_reserved_test_origins():
    real = _article(
        "real-origin",
        title="Official AI product launch",
        tier="t1",
        category=TopicCategory.AI_PRODUCTS,
    ).model_copy(
        update={"source_url": "https://official-source.ai/releases/product"}
    )
    reserved = real.model_copy(
        update={"source_url": "https://fixture.example/acceptance"}
    )

    assert is_evaluation_eligible_article(real) is True
    assert is_evaluation_eligible_article(reserved) is False


def test_sample_gold_file_validates_and_replays(tmp_path):
    sample_path = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "evaluation"
        / "gold"
        / "news-gold-v1.sample.jsonl"
    )
    report_path = tmp_path / "report.json"

    assert main(["validate", "--input", str(sample_path)]) == 0
    assert main([
        "replay",
        "--input",
        str(sample_path),
        "--output",
        str(report_path),
    ]) == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["sampleCount"] == 2


def test_release_gate_cli_writes_blocked_report_in_report_only_mode(tmp_path):
    report_path = tmp_path / "r1-report.json"
    database_url = f"sqlite:///{tmp_path / 'empty.db'}"

    assert main([
        "release-gate",
        "--database-url",
        database_url,
        "--gold",
        str(tmp_path / "missing.jsonl"),
        "--output",
        str(report_path),
        "--report-only",
    ]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert {item["code"] for item in report["blockers"]} == {
        "RELIABILITY_WINDOW_INCOMPLETE",
        "GOLD_FILE_MISSING",
    }


def test_export_gold_refuses_to_overwrite_without_force(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'empty.db'}"
    output = tmp_path / "news-gold-v1.jsonl"
    output.write_text("immutable", encoding="utf-8")

    result = main([
        "export-gold",
        "--database-url",
        database_url,
        "--output",
        str(output),
    ])

    assert result == 2
    assert output.read_text(encoding="utf-8") == "immutable"


def test_export_gold_overwrites_only_with_explicit_force(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'empty-force.db'}"
    output = tmp_path / "news-gold-v1.1.jsonl"
    output.write_text("old", encoding="utf-8")

    result = main([
        "export-gold",
        "--database-url",
        database_url,
        "--output",
        str(output),
        "--force",
    ])

    assert result == 0
    assert output.read_text(encoding="utf-8") == ""
