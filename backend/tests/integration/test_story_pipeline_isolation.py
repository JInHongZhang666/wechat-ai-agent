import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_agent.api.routes import ContentApiService
from wechat_agent.collectors.base import LiveCollectionResult
from wechat_agent.domain.models import (
    ArticleRecord,
    SourceRun,
    SourceRunStatus,
    TopicCategory,
)
from wechat_agent.services.pipeline import ContentPipeline, TaskStatus
from wechat_agent.storage.memory import (
    InMemoryArticleRepository,
    InMemoryCurationDecisionRepository,
    InMemorySourceRunRepository,
)
from wechat_agent.writing.provider import RuleBasedDraftProvider


SOURCE_ID = "official:story-isolation"


class DeterministicLiveCollector:
    def __init__(self) -> None:
        self.last_article: ArticleRecord | None = None

    def collect(self, now):
        article = ArticleRecord(
            id="story-isolation-article",
            source_id=SOURCE_ID,
            title="Qwen 3.5 正式发布",
            source_name="Qwen Official",
            source_url="https://example.com/qwen-3-5",
            published_at=now - timedelta(minutes=5),
            collected_at=now,
            summary="Qwen releases the Qwen 3.5 model.",
            category=TopicCategory.AI_MODELS,
            source_confidence=1.0,
            source_type="official",
            source_identity=SOURCE_ID,
            source_type_verified=True,
            tier="t1",
        )
        self.last_article = article
        return LiveCollectionResult(
            articles=(article,),
            window_days=7,
            source_counts={SOURCE_ID: 1},
            source_runs=(
                SourceRun(
                    id=f"run-{now.timestamp()}",
                    source_id=SOURCE_ID,
                    window_days=7,
                    started_at=now - timedelta(seconds=1),
                    finished_at=now,
                    status=SourceRunStatus.SUCCESS,
                    fetched_count=1,
                    duration_ms=1000,
                ),
            ),
        )


class RecordingStoryShadowService:
    def __init__(self, articles, decisions) -> None:
        self.articles = articles
        self.decisions = decisions
        self.calls = []

    def process(self, articles, now):
        rows = tuple(articles)
        persisted = self.articles.list_all()
        decisions = self.decisions.get_many([article.id for article in rows])
        self.calls.append(
            SimpleNamespace(
                articles=rows,
                now=now,
                persisted_ids={article.id for article in persisted},
                decision_ids=set(decisions),
            )
        )


class ExplodingStoryShadowService(RecordingStoryShadowService):
    def __init__(self, articles, decisions) -> None:
        super().__init__(articles, decisions)
        self.failures = []

    def process(self, articles, now):
        super().process(articles, now)
        raise RuntimeError("Story shadow exploded")

    def record_failure(self, started_at, error):
        self.failures.append((started_at, str(error)))


@pytest.fixture
def service_factory(tmp_path):
    def factory(story_type=RecordingStoryShadowService):
        article_repository = InMemoryArticleRepository()
        decision_repository = InMemoryCurationDecisionRepository()
        source_run_repository = InMemorySourceRunRepository()
        story = story_type(article_repository, decision_repository)
        collector = DeterministicLiveCollector()
        service = ContentApiService(
            ContentPipeline(
                article_repository,
                curation_repository=decision_repository,
                enable_pre_screen=False,
            ),
            tmp_path / "unused.json",
            RuleBasedDraftProvider(),
            collector,
            source_run_repo=source_run_repository,
            story_shadow_service=story,
        )
        return SimpleNamespace(
            service=service,
            story=story,
            collector=collector,
            source_runs=source_run_repository,
        )

    return factory


def test_live_collection_invokes_story_after_distribution_persistence(service_factory):
    fixture = service_factory()

    task = fixture.service.create_live_task()

    assert task is not None
    assert task.status is TaskStatus.REVIEW_READY
    assert len(fixture.story.calls) == 1
    call = fixture.story.calls[0]
    assert call.articles
    assert {article.id for article in call.articles} <= call.persisted_ids
    assert {article.id for article in call.articles} <= call.decision_ids


def test_story_failure_does_not_fail_collection_or_source_run(service_factory):
    fixture = service_factory(ExplodingStoryShadowService)

    task = fixture.service.create_live_task()

    assert task is not None
    assert task.status is TaskStatus.REVIEW_READY
    runs = fixture.source_runs.list_recent(
        SOURCE_ID,
        datetime.now(UTC) - timedelta(minutes=5),
    )
    assert len(runs) == 1
    assert runs[0].status is SourceRunStatus.SUCCESS
    assert len(fixture.story.failures) == 1
    assert "Story shadow exploded" in fixture.story.failures[0][1]


def test_demo_task_does_not_invoke_formal_story_shadow(tmp_path):
    article_repository = InMemoryArticleRepository()
    decision_repository = InMemoryCurationDecisionRepository()
    story = RecordingStoryShadowService(article_repository, decision_repository)
    fixture_path = tmp_path / "demo.json"
    demo_article = DeterministicLiveCollector().collect(datetime.now(UTC)).articles[0]
    fixture_path.write_text(
        json.dumps([demo_article.model_dump(mode="json")], ensure_ascii=False),
        encoding="utf-8",
    )
    service = ContentApiService(
        ContentPipeline(
            article_repository,
            curation_repository=decision_repository,
            enable_pre_screen=False,
        ),
        fixture_path,
        RuleBasedDraftProvider(),
        story_shadow_service=story,
    )

    task = service.create_demo_task()

    assert task.status is TaskStatus.REVIEW_READY
    assert story.calls == []
