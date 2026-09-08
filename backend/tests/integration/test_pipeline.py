import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from wechat_agent.config import LlmConfig
from wechat_agent.domain.models import (
    ArticleRecord,
    CurationDecision,
    HeatConfidence,
    HeatScore,
    TopicCategory,
)
from wechat_agent.services.pipeline import (
    ContentPipeline,
    DISTRIBUTION_STRATEGY_VERSION,
    PipelineResult,
)
from wechat_agent.storage.sqlite import (
    SqliteArticleRepository,
    SqliteCurationDecisionRepository,
)


NOW = datetime(2026, 7, 25, 12, tzinfo=UTC)
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "articles.json"


def _fixture_articles() -> list[ArticleRecord]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [ArticleRecord.model_validate(item) for item in payload]


def test_pipeline_persists_articles_and_selects_five_plus_five(tmp_path):
    repository = SqliteArticleRepository(f"sqlite:///{tmp_path / 'pipeline.db'}")
    articles = _fixture_articles()

    result = ContentPipeline(repository).run(articles, NOW)

    assert result.saved_article_count == len(articles)
    assert sum(
        item.category is TopicCategory.TOOL for item in result.candidates
    ) == 5
    assert sum(
        item.category is TopicCategory.NEWS for item in result.candidates
    ) == 5
    assert len(repository.list_recent(datetime(2020, 1, 1, tzinfo=UTC))) == len(
        articles
    )


def test_pipeline_preserves_traceable_candidate_to_related_article_mapping(
    tmp_path,
):
    repository = SqliteArticleRepository(f"sqlite:///{tmp_path / 'pipeline.db'}")
    articles = _fixture_articles()

    result = ContentPipeline(repository).run(articles, NOW)

    input_urls = {article.source_url for article in articles}
    assert set(result.related_articles_by_topic) == {
        candidate.id for candidate in result.candidates
    }
    for candidate in result.candidates:
        related = result.related_articles_by_topic[candidate.id]
        assert related
        assert candidate.id in {article.id for article in related}
        assert {article.source_url for article in related} <= input_urls


def test_pipeline_reports_real_shortages_without_fabricating_topics(tmp_path):
    repository = SqliteArticleRepository(f"sqlite:///{tmp_path / 'pipeline.db'}")
    only_one_tool = [_fixture_articles()[0]]

    result = ContentPipeline(repository).run(only_one_tool, NOW)

    assert [candidate.id for candidate in result.candidates] == ["tool-1"]
    assert result.shortage == {
        TopicCategory.TOOL: 4,
        TopicCategory.NEWS: 5,
    }


def test_pipeline_skips_unchanged_collector_content_before_llm(
    tmp_path,
    monkeypatch,
):
    repository = SqliteArticleRepository(f"sqlite:///{tmp_path / 'incremental.db'}")
    source_article = _fixture_articles()[0]
    config = LlmConfig(
        api_key="test-key",
        base_url="https://incremental.test/v1",
    )
    pre_screen = Mock(side_effect=lambda articles, _: list(articles))
    classify = Mock(
        side_effect=lambda articles, _: [
            article.model_copy(update={"summary": "AI 生成的新摘要"})
            for article in articles
        ]
    )

    def fake_score(articles, _now, **_kwargs):
        return {
            article.id: HeatScore(
                value=80,
                confidence=HeatConfidence.MEASURED,
                components={
                    "authority": 80,
                    "novelty": 80,
                    "impact": 80,
                    "practicality": 80,
                },
            )
            for article in articles
        }

    scorer = Mock(side_effect=fake_score)
    monkeypatch.setattr(
        "wechat_agent.services.pipeline.pre_screen_sync",
        pre_screen,
    )
    monkeypatch.setattr(
        "wechat_agent.services.pipeline.classify_and_summarize",
        classify,
    )
    monkeypatch.setattr("wechat_agent.services.pipeline.score_articles", scorer)
    pipeline = ContentPipeline(repository, llm_config=config)

    first = pipeline.run([source_article], NOW)
    repeated = source_article.model_copy(
        update={"collected_at": NOW + timedelta(hours=1)}
    )
    second = pipeline.run([repeated], NOW + timedelta(hours=1))

    assert first.saved_article_count == 1
    assert second.saved_article_count == 0
    assert second.skipped_unchanged_count == 1
    assert second.articles == []
    assert pre_screen.call_count == 1
    assert classify.call_count == 1
    assert scorer.call_count == 1
    assert repository.list_all()[0].summary == "AI 生成的新摘要"

    changed = repeated.model_copy(update={"summary": "采集端原文确实发生变化"})
    third = pipeline.run([changed], NOW + timedelta(hours=2))

    assert third.saved_article_count == 1
    assert pre_screen.call_count == 2
    assert classify.call_count == 2


def test_pipeline_remembers_pre_screen_rejections(tmp_path, monkeypatch):
    repository = SqliteArticleRepository(f"sqlite:///{tmp_path / 'rejected.db'}")
    article = _fixture_articles()[0]
    config = LlmConfig(
        api_key="test-key",
        base_url="https://rejected.test/v1",
    )
    pre_screen = Mock(return_value=[])
    monkeypatch.setattr(
        "wechat_agent.services.pipeline.pre_screen_sync",
        pre_screen,
    )
    pipeline = ContentPipeline(repository, llm_config=config)

    first = pipeline.run([article], NOW)
    second = pipeline.run(
        [article.model_copy(update={"collected_at": NOW + timedelta(hours=1)})],
        NOW + timedelta(hours=1),
    )

    assert first.saved_article_count == 1
    assert first.pre_screened_count == 0
    assert second.saved_article_count == 0
    assert second.skipped_unchanged_count == 1
    assert pre_screen.call_count == 1


def test_legacy_feed_flags_are_recomputed_from_current_thresholds(tmp_path):
    article_repository = SqliteArticleRepository(
        f"sqlite:///{tmp_path / 'legacy-decision.db'}"
    )
    decision_repository = SqliteCurationDecisionRepository(article_repository.engine)
    article = _fixture_articles()[0].model_copy(
        update={
            "tier": "t2",
            "published_at": NOW - timedelta(days=7),
            "collected_at": NOW,
        }
    )
    score = HeatScore(
        value=55,
        confidence=HeatConfidence.ESTIMATED,
        components={"authority": 32.5, "novelty": 25, "impact": 25, "practicality": 25},
    )
    article_repository.save_many([article])
    decision_repository.save_many([
        CurationDecision(
            article_id=article.id,
            distribution_score=score.value,
            selected=True,
            components=score.components,
            threshold=70,
            reason="legacy fallback",
            score_confidence=score.confidence,
            strategy_version="legacy-task-migration-v1",
            scored_at=NOW,
        )
    ])
    result = PipelineResult(
        saved_article_count=1,
        candidates=[],
        related_articles_by_topic={},
        articles=[article],
        scores={article.id: score},
        selected_article_ids=[article.id],
    )
    pipeline = ContentPipeline(
        article_repository,
        curation_repository=decision_repository,
    )

    assert pipeline.backfill_legacy_result(result) == 1
    migrated = decision_repository.get_many([article.id])[article.id]
    assert migrated.selected is False
    assert migrated.strategy_version == DISTRIBUTION_STRATEGY_VERSION
    assert "未达精选门槛" in migrated.reason
