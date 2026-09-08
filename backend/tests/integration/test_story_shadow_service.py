from dataclasses import dataclass
from datetime import timedelta
from threading import Event, Thread

import pytest

from tests.story_factories import NOW, make_story_article
from wechat_agent.domain.models import (
    CurationDecision,
    HeatConfidence,
    Source,
    SourceKind,
    SourceTier,
    TopicCategory,
)
from wechat_agent.storage.sqlite import (
    SqliteArticleRepository,
    SqliteCurationDecisionRepository,
    SqliteSourceRepository,
)
from wechat_agent.storage.story_sqlite import SqliteStoryRepository
from wechat_agent.story.service import StoryShadowService


@dataclass(frozen=True)
class StoryRepos:
    article: SqliteArticleRepository
    curation: SqliteCurationDecisionRepository
    source: SqliteSourceRepository
    story: SqliteStoryRepository


@dataclass(frozen=True)
class UpdateFixture:
    first_two: tuple
    update: object


@pytest.fixture
def repos(tmp_path):
    article = SqliteArticleRepository(f"sqlite:///{tmp_path / 'service.db'}")
    return StoryRepos(
        article=article,
        curation=SqliteCurationDecisionRepository(article.engine),
        source=SqliteSourceRepository(article.engine),
        story=SqliteStoryRepository(article.engine),
    )


@pytest.fixture
def service(repos):
    return StoryShadowService(
        repos.article,
        repos.curation,
        repos.source,
        repos.story,
    )


def _decision(article_id: str, *, eligible: bool = True) -> CurationDecision:
    return CurationDecision(
        article_id=article_id,
        distribution_score=80,
        selected=eligible,
        eligible=eligible,
        components={"authority": 80},
        threshold=60,
        reason="Story shadow service fixture.",
        score_confidence=HeatConfidence.MEASURED,
        scored_at=NOW,
    )


def _source(source_id: str, publisher_group: str) -> Source:
    return Source(
        id=source_id,
        name=source_id,
        kind=SourceKind.RSS,
        tier=SourceTier.T1,
        publisher_group=publisher_group,
        categories=(TopicCategory.AI_MODELS,),
        created_at=NOW,
        updated_at=NOW,
    )


def _save(repos: StoryRepos, articles, *, rejected_ids=()):
    articles = tuple(articles)
    repos.article.save_many(articles)
    repos.curation.save_many(
        [
            _decision(article.id, eligible=article.id not in rejected_ids)
            for article in articles
        ]
    )


def seed_story_fixture(repos: StoryRepos):
    official = make_story_article(
        id="official",
        source_id="official:qwen",
        source_identity="official:qwen",
        source_type="official",
        tier="t1",
        title="Qwen 3.5 正式发布",
    )
    coverage = make_story_article(
        id="coverage",
        source_id="publisher:a",
        source_identity="publisher:a",
        title="Qwen 3.5 正式发布",
        published_at=NOW + timedelta(hours=2),
    )
    rejected = make_story_article(
        id="rejected",
        source_id="publisher:noise",
        source_identity="publisher:noise",
        title="Qwen 3.5 正式发布",
        published_at=NOW + timedelta(hours=1),
    )
    _save(repos, (official, coverage, rejected), rejected_ids={rejected.id})
    repos.source.seed_many(
        (
            _source("official:qwen", "qwen-official"),
            _source("publisher:a", "publisher-a"),
            _source("publisher:noise", "noise"),
        )
    )
    return official, coverage, rejected


def _update_fixture(repos: StoryRepos) -> UpdateFixture:
    official, coverage, _ = seed_story_fixture(repos)
    update = make_story_article(
        id="update",
        source_id="publisher:update",
        source_identity="publisher:update",
        title="Qwen 3.5 正式发布",
        published_at=NOW + timedelta(hours=3),
    )
    repos.source.seed_many((_source("publisher:update", "publisher-update"),))
    return UpdateFixture(first_two=(official, coverage), update=update)


def test_replay_builds_story_only_from_eligible_articles(service, repos):
    seed_story_fixture(repos)

    run = service.replay(days=7, now=NOW + timedelta(hours=4))

    assert run.status == "success"
    assert run.input_article_count == 2
    page = repos.story.list_stories(status=None, offset=0, limit=20)
    assert page.total == 1
    assert page.items[0].report_count == 2


def test_unchanged_incremental_input_is_a_persisted_noop(service, repos):
    article = make_story_article(id="incremental")
    _save(repos, (article,))
    repos.source.seed_many((_source("publisher:test", "publisher-test"),))

    first = service.process([article], NOW)
    second = service.process([article], NOW + timedelta(minutes=5))

    assert first.processed_article_count == 1
    assert second.processed_article_count == 0
    assert second.created_revision_count == 0
    assert repos.story.latest_run() == second


def test_existing_public_id_survives_new_report(service, repos):
    fixture = _update_fixture(repos)
    service.process(fixture.first_two, NOW + timedelta(hours=2))
    before = repos.story.list_stories(status=None, offset=0, limit=10).items[0]
    _save(repos, (fixture.update,))

    service.process([fixture.update], NOW + timedelta(hours=3))

    after = repos.story.list_stories(status=None, offset=0, limit=10).items[0]
    assert after.public_id == before.public_id
    assert after.revision == before.revision + 1
    assert after.report_count == 3


def test_restart_reuses_persisted_fingerprint_and_story_identity(service, repos):
    official, coverage, _ = seed_story_fixture(repos)
    service.process((official, coverage), NOW + timedelta(hours=2))
    before = repos.story.list_stories(status=None, offset=0, limit=10).items[0]

    restarted_article = SqliteArticleRepository(str(repos.article.engine.url))
    restarted_story = SqliteStoryRepository(restarted_article.engine)
    restarted = StoryShadowService(
        restarted_article,
        SqliteCurationDecisionRepository(restarted_article.engine),
        SqliteSourceRepository(restarted_article.engine),
        restarted_story,
    )

    run = restarted.process((official, coverage), NOW + timedelta(hours=3))
    after = restarted_story.list_stories(status=None, offset=0, limit=10).items[0]

    assert run.processed_article_count == 0
    assert after.public_id == before.public_id
    assert after.revision == before.revision
    assert after.report_count == before.report_count


def test_concurrent_run_is_skipped_by_single_flight(repos):
    article = make_story_article(id="single-flight")
    _save(repos, (article,))
    entered = Event()
    release = Event()

    class BlockingArticleRepository:
        def list_recent(self, since, limit=100):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release blocked Story run")
            return repos.article.list_recent(since, limit)

    service = StoryShadowService(
        BlockingArticleRepository(),
        repos.curation,
        repos.source,
        repos.story,
    )
    completed = []
    failures = []

    def run_first():
        try:
            completed.append(service.process((article,), NOW))
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    thread = Thread(target=run_first)
    thread.start()
    assert entered.wait(timeout=5)

    skipped = service.process((article,), NOW + timedelta(minutes=1))
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert failures == []
    assert len(completed) == 1
    assert completed[0].status == "success"
    assert skipped.status == "skipped"
    assert skipped.skip_reason == "single_flight"


@pytest.mark.parametrize("days", [0, 8])
def test_replay_rejects_windows_outside_one_to_seven_days(service, days):
    with pytest.raises(ValueError, match="between 1 and 7"):
        service.replay(days=days, now=NOW)


def test_record_failure_redacts_provider_secrets(service, repos):
    run = service.record_failure(
        NOW,
        RuntimeError("provider rejected sk-secret-value and Bearer abc123"),
    )

    assert run.status == "failed"
    assert run.error_code == "story_shadow_failure"
    assert "sk-secret-value" not in (run.error_summary or "")
    assert "abc123" not in (run.error_summary or "")
    assert repos.story.latest_run() == run
