from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from inspect import iscoroutinefunction
import json
import sqlite3

import pytest

from wechat_agent.domain.models import (
    ArticleRecord,
    CurationDecision,
    CircuitState,
    EngagementMetrics,
    HeatConfidence,
    Source,
    SourceKind,
    SourceRun,
    SourceRunStatus,
    SourceTier,
    TopicCategory,
)
from wechat_agent.domain.ports import (
    ArticleRepository,
    CurationDecisionRepository,
    SourceRepository,
    SourceRunRepository,
)
from wechat_agent.storage.sqlite import (
    SqliteArticleRepository,
    SqliteCurationDecisionRepository,
    SqliteSourceRepository,
    SqliteSourceRunRepository,
)


@pytest.fixture
def sample_article() -> ArticleRecord:
    return ArticleRecord(
        id="article-1",
        title="A useful AI tool",
        source_name="Example source",
        source_url="https://example.com/articles/ai-tool",
        published_at=datetime(2026, 7, 25, 8, tzinfo=UTC),
        collected_at=datetime(2026, 7, 25, 9, tzinfo=UTC),
        summary="A concise description of the tool.",
        category=TopicCategory.TOOL,
        source_type="official",
        source_identity="example-publisher",
        source_type_verified=True,
        source_verification_basis="collector verified example.com ownership",
        engagement=EngagementMetrics(views=20, likes=0, comments=2, shares=1),
        source_confidence=0.93,
        tier="t1",
    )


def test_repository_round_trip(tmp_path, sample_article):
    repo = SqliteArticleRepository(f"sqlite:///{tmp_path / 'test.db'}")

    assert repo.save_many([sample_article]) == 1
    loaded = repo.list_recent(sample_article.published_at)

    assert loaded == [sample_article]
    assert loaded[0].source_confidence == 0.93
    assert loaded[0].tier == "t1"


def test_curation_decision_round_trip(tmp_path, sample_article):
    article_repo = SqliteArticleRepository(f"sqlite:///{tmp_path / 'decision.db'}")
    repository: CurationDecisionRepository = SqliteCurationDecisionRepository(
        article_repo.engine
    )
    decision = CurationDecision(
        article_id=sample_article.id,
        distribution_score=82.5,
        selected=True,
        components={
            "authority": 100,
            "novelty": 80,
            "impact": 75,
            "practicality": 70,
        },
        threshold=60,
        reason="T1 来源且质量达到精选阈值。",
        score_confidence=HeatConfidence.MEASURED,
        strategy_version="distribution-v1",
        scored_at=sample_article.collected_at,
    )

    assert repository.save_many([decision]) == 1
    assert repository.get_many([sample_article.id]) == {
        sample_article.id: decision
    }


def test_in_memory_repositories_are_isolated(sample_article):
    first = SqliteArticleRepository("sqlite:///:memory:")
    second = SqliteArticleRepository("sqlite:///:memory:")

    first.save_many([sample_article])

    assert first.list_recent(sample_article.published_at) == [sample_article]
    assert second.list_recent(sample_article.published_at) == []


def test_explicit_sqlite_path_remains_shared_between_repositories(
    tmp_path, sample_article
):
    database_url = f"sqlite:///{tmp_path / 'shared.db'}"
    first = SqliteArticleRepository(database_url)
    second = SqliteArticleRepository(database_url)

    first.save_many([sample_article])

    assert second.list_recent(sample_article.published_at) == [sample_article]


def test_repository_upserts_records_with_the_same_source_url(tmp_path, sample_article):
    repo = SqliteArticleRepository(f"sqlite:///{tmp_path / 'test.db'}")
    replacement = sample_article.model_copy(
        update={
            "id": "article-2",
            "title": "An updated AI tool",
            "summary": "An updated description.",
        }
    )

    repo.save_many([sample_article])
    assert repo.save_many([replacement]) == 1

    assert repo.list_recent(sample_article.published_at) == [replacement]


def test_repository_loads_legacy_source_metadata_with_safe_defaults(
    tmp_path, sample_article
):
    database_path = tmp_path / "test.db"
    repo = SqliteArticleRepository(f"sqlite:///{database_path}")
    repo.save_many([sample_article])
    legacy_metadata = {
        "source_name": sample_article.source_name,
        "source_url": sample_article.source_url,
    }
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE articles SET source_metadata = ? WHERE source_url = ?",
            (json.dumps(legacy_metadata), sample_article.source_url),
        )

    loaded = repo.list_recent(sample_article.published_at)[0]

    assert loaded.source_type.value == "secondary"
    assert loaded.source_identity is None
    assert loaded.source_type_verified is False
    assert loaded.source_verification_basis is None


def test_repository_satisfies_sync_port_and_honors_recent_limit(
    tmp_path, sample_article
):
    assert not iscoroutinefunction(ArticleRepository.save_many)
    assert not iscoroutinefunction(ArticleRepository.list_recent)

    repository: ArticleRepository = SqliteArticleRepository(
        f"sqlite:///{tmp_path / 'test.db'}"
    )
    later_article = sample_article.model_copy(
        update={
            "id": "article-2",
            "source_url": "https://example.com/articles/later-ai-tool",
            "published_at": datetime(2026, 7, 25, 10, tzinfo=UTC),
            "engagement": EngagementMetrics(likes=None),
        }
    )

    assert repository.save_many([sample_article, later_article]) == 2

    recent = repository.list_recent(sample_article.published_at, limit=1)

    assert recent == [later_article]
    assert recent[0].engagement is not None
    assert recent[0].engagement.likes is None


def test_source_seed_preserves_operator_state(tmp_path):
    article_repo = SqliteArticleRepository(f"sqlite:///{tmp_path / 'sources.db'}")
    repository: SourceRepository = SqliteSourceRepository(article_repo.engine)
    now = datetime(2026, 8, 2, tzinfo=UTC)
    seed = Source(
        id="official:example",
        name="Example AI",
        homepage="https://example.com",
        feed_url="https://example.com/feed.xml",
        kind=SourceKind.RSS,
        tier=SourceTier.T1,
        publisher_group="official:example",
        categories=(TopicCategory.AI_MODELS,),
        language="en",
        schedule_minutes=10,
        circuit_state=CircuitState.CLOSED,
        created_at=now,
        updated_at=now,
    )

    assert repository.seed_many([seed]) == 1
    operator_update = seed.model_copy(
        update={
            "enabled": False,
            "tier": SourceTier.T2,
            "last_error_code": "operator_disabled",
            "lease_owner": "task-in-progress",
            "lease_expires_at": datetime(2026, 8, 2, 2, tzinfo=UTC),
            "updated_at": datetime(2026, 8, 2, 1, tzinfo=UTC),
        }
    )
    repository.save(operator_update)

    refreshed_seed = seed.model_copy(
        update={
            "name": "Example AI Updated",
            "feed_url": "https://example.com/feed-v2.xml",
            "parser_version": "rss-atom-v2",
        }
    )
    assert repository.seed_many([refreshed_seed]) == 0
    loaded = repository.get(seed.id)

    assert loaded is not None
    assert loaded.name == "Example AI Updated"
    assert loaded.feed_url == "https://example.com/feed-v2.xml"
    assert loaded.parser_version == "rss-atom-v2"
    assert loaded.enabled is False
    assert loaded.tier is SourceTier.T2
    assert loaded.last_error_code == "operator_disabled"
    assert loaded.lease_owner == "task-in-progress"
    assert loaded.lease_expires_at == datetime(2026, 8, 2, 2, tzinfo=UTC)
    assert loaded.updated_at == operator_update.updated_at
    assert repository.list_all() == [loaded]


def test_source_seed_resets_open_circuit_when_collector_contract_changes(tmp_path):
    article_repo = SqliteArticleRepository(f"sqlite:///{tmp_path / 'source-reset.db'}")
    repository: SourceRepository = SqliteSourceRepository(article_repo.engine)
    now = datetime(2026, 8, 2, tzinfo=UTC)
    old_source = Source(
        id="aggregator:hacker-news",
        name="Hacker News AI",
        homepage="https://news.ycombinator.com",
        feed_url="https://hnrss.org/frontpage?q=AI",
        kind=SourceKind.RSS,
        tier=SourceTier.T2,
        publisher_group="aggregator:hacker-news",
        categories=(TopicCategory.INDUSTRY,),
        enabled=True,
        parser_version="rss-atom-v1",
        circuit_state=CircuitState.OPEN,
        consecutive_failures=3,
        next_run_at=datetime(2026, 8, 2, 4, tzinfo=UTC),
        last_error_code="http_502",
        created_at=now,
        updated_at=now,
    )
    repository.save(old_source)
    new_source = old_source.model_copy(
        update={
            "feed_url": "https://hacker-news.firebaseio.com/v0/topstories.json",
            "kind": SourceKind.API,
            "parser_version": "hacker-news-official-api-v1",
            "circuit_state": CircuitState.CLOSED,
            "consecutive_failures": 0,
            "next_run_at": None,
            "last_error_code": None,
        }
    )

    assert repository.seed_many([new_source]) == 0
    loaded = repository.get(old_source.id)

    assert loaded is not None
    assert loaded.feed_url == new_source.feed_url
    assert loaded.kind is SourceKind.API
    assert loaded.parser_version == "hacker-news-official-api-v1"
    assert loaded.circuit_state is CircuitState.CLOSED
    assert loaded.consecutive_failures == 0
    assert loaded.next_run_at is None
    assert loaded.last_error_code is None
    assert loaded.enabled is True
    assert loaded.tier is SourceTier.T2


def test_source_retirement_migration_preserves_history_and_scope(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'source-retirement.db'}"
    article_repo = SqliteArticleRepository(database_url)
    repository = SqliteSourceRepository(article_repo.engine)
    now = datetime(2026, 8, 2, tzinfo=UTC)

    def source(source_id: str) -> Source:
        return Source(
            id=source_id,
            name=source_id,
            feed_url=f"https://example.com/{source_id}.xml",
            kind=SourceKind.RSS,
            tier=SourceTier.T1,
            publisher_group=source_id,
            categories=(TopicCategory.AI_MODELS,),
            enabled=True,
            circuit_state=CircuitState.OPEN,
            consecutive_failures=6,
            next_run_at=now + timedelta(hours=1),
            last_error_code="http_timeout",
            lease_owner="stale-task",
            lease_expires_at=now + timedelta(minutes=15),
            created_at=now,
            updated_at=now,
        )

    retired_ids = (
        "official:google-ai",
        "official:hugging-face",
        "official:mistral-ai",
    )
    repository.seed_many([source(source_id) for source_id in retired_ids])
    repository.seed_many([source("official:still-active")])
    with article_repo.engine.begin() as connection:
        connection.exec_driver_sql(
            "DELETE FROM schema_migrations WHERE version = 13"
        )

    migrated_repo = SqliteArticleRepository(database_url)
    migrated_sources = SqliteSourceRepository(migrated_repo.engine)

    for source_id in retired_ids:
        loaded = migrated_sources.get(source_id)
        assert loaded is not None
        assert loaded.enabled is False
        assert loaded.last_error_code == "retired_runtime_unreachable_feed"
        assert loaded.next_run_at is None
        assert loaded.lease_owner is None
        assert loaded.lease_expires_at is None
        assert loaded.consecutive_failures == 6
    control = migrated_sources.get("official:still-active")
    assert control is not None
    assert control.enabled is True
    assert control.last_error_code == "http_timeout"


def test_source_run_round_trip_and_recent_filter(tmp_path):
    article_repo = SqliteArticleRepository(f"sqlite:///{tmp_path / 'runs.db'}")
    repository: SourceRunRepository = SqliteSourceRunRepository(article_repo.engine)
    run = SourceRun(
        id="run-1",
        source_id="official:example",
        task_id="task-1",
        window_days=30,
        started_at=datetime(2026, 8, 2, 1, tzinfo=UTC),
        finished_at=datetime(2026, 8, 2, 1, 0, 1, tzinfo=UTC),
        status=SourceRunStatus.PARTIAL,
        http_status=200,
        fetched_count=10,
        stored_count=8,
        skipped_count=2,
        error_code="partial_parse",
        error_summary="Two entries did not contain a date.",
        retry_after_seconds=120,
        duration_ms=1000,
    )

    repository.save(run)

    assert repository.list_recent(
        run.source_id,
        datetime(2026, 8, 2, tzinfo=UTC),
    ) == [run]
    assert repository.list_recent(
        run.source_id,
        datetime(2026, 8, 2, 2, tzinfo=UTC),
    ) == []


def test_versioned_migrations_upgrade_phase_2a_database(tmp_path):
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE articles (
              source_url VARCHAR PRIMARY KEY,
              id VARCHAR NOT NULL,
              title VARCHAR NOT NULL,
              published_at VARCHAR NOT NULL,
              collected_at VARCHAR NOT NULL,
              summary VARCHAR NOT NULL,
              category VARCHAR NOT NULL,
              engagement JSON,
              source_metadata JSON NOT NULL,
              content_fingerprint VARCHAR NOT NULL
            );
            CREATE TABLE curation_decisions (
              article_id VARCHAR PRIMARY KEY,
              distribution_score FLOAT NOT NULL,
              selected BOOLEAN NOT NULL,
              components JSON NOT NULL,
              threshold FLOAT NOT NULL,
              reason TEXT NOT NULL,
              score_confidence VARCHAR NOT NULL,
              strategy_version VARCHAR NOT NULL,
              scored_at VARCHAR NOT NULL,
              updated_at VARCHAR NOT NULL
            );
            INSERT INTO articles VALUES (
              'https://example.com/legacy',
              'legacy-article',
              'Legacy article',
              '2026-08-01T00:00:00+00:00',
              '2026-08-01T00:01:00+00:00',
              'Legacy summary',
              'industry',
              NULL,
              '{"source_name":"Legacy","source_identity":"publisher:legacy"}',
              'fingerprint'
            );
            """
        )

    SqliteArticleRepository(f"sqlite:///{database_path}")

    with sqlite3.connect(database_path) as connection:
        article_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(articles)")
        }
        decision_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(curation_decisions)")
        }
        source_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sources)")
        }
        run_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(source_runs)")
        }
        daily_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(daily_reports)")
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        migrated_source_id = connection.execute(
            "SELECT source_id FROM articles WHERE id = 'legacy-article'"
        ).fetchone()[0]
        article_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(articles)")
        }

    assert "source_id" in article_columns
    assert "eligible" in decision_columns
    assert {
        "sources",
        "source_runs",
        "daily_reports",
        "schema_migrations",
        "selected_sync_items",
        "selected_sync_changes",
        "selected_sync_state",
        "selected_sync_cursors",
        "selected_sync_snapshots",
        "selected_sync_snapshot_pages",
        "evaluation_candidates",
        "evaluation_annotations",
            "evaluation_review_sets",
            "evaluation_review_items",
            "evaluation_review_participants",
            "evaluation_review_submissions",
        "evaluation_review_adjudications",
            "creator_projects",
            "account_profiles",
            "content_opportunities",
            "prompt_versions",
            "llm_call_records",
            "creation_replay_reports",
            "stories",
            "story_reports",
            "story_revisions",
            "story_relations",
            "story_candidate_pairs",
            "story_article_states",
            "story_shadow_runs",
    } <= tables
    assert {"lease_owner", "lease_expires_at"} <= source_columns
    assert {
        "retry_after_seconds",
        "stage",
        "task_id",
        "reliability_excluded_reason",
    } <= run_columns
    assert {
        "report_date",
        "revision",
        "timezone",
        "payload_json",
        "content_hash",
    } <= daily_columns
    assert versions == [
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18
    ]
    assert {
        "ix_articles_published_id",
        "ix_articles_collected_id",
    } <= article_indexes
    assert migrated_source_id == "publisher:legacy"


def test_v17_migration_backfills_only_confirmed_global_outage_batches(tmp_path):
    database_path = tmp_path / "outage-backfill.db"
    article_repo = SqliteArticleRepository(f"sqlite:///{database_path}")
    run_repository = SqliteSourceRunRepository(article_repo.engine)
    started = datetime(2026, 8, 15, 4, tzinfo=UTC)

    for index in range(4):
        run_repository.save(SourceRun(
            id=f"broad-{index}", source_id=f"official:{index}",
            task_id="task-broad", started_at=started,
            finished_at=started + timedelta(seconds=1),
            status=SourceRunStatus.FAILED, error_code="http_timeout",
            duration_ms=1000,
        ))
    for index in range(3):
        run_repository.save(SourceRun(
            id=f"probe-{index}", source_id=f"official:{index}",
            task_id="task-probe", started_at=started + timedelta(minutes=5),
            finished_at=started + timedelta(minutes=5, seconds=1),
            status=SourceRunStatus.FAILED, error_code="network_error",
            duration_ms=1000,
        ))
    run_repository.save(SourceRun(
        id="recovered", source_id="official:0", task_id="task-recovered",
        started_at=started + timedelta(minutes=10),
        finished_at=started + timedelta(minutes=10, seconds=1),
        status=SourceRunStatus.SUCCESS, duration_ms=1000,
    ))
    run_repository.save(SourceRun(
        id="ordinary-failure", source_id="official:0", task_id="task-ordinary",
        started_at=started + timedelta(minutes=15),
        finished_at=started + timedelta(minutes=15, seconds=1),
        status=SourceRunStatus.FAILED, error_code="http_timeout",
        duration_ms=1000,
    ))

    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = 17")
        connection.execute(
            "ALTER TABLE source_runs DROP COLUMN reliability_excluded_reason"
        )

    SqliteArticleRepository(f"sqlite:///{database_path}")

    with sqlite3.connect(database_path) as connection:
        excluded = {
            row[0]
            for row in connection.execute(
                "SELECT id FROM source_runs "
                "WHERE reliability_excluded_reason = 'global_network_outage'"
            )
        }
        ordinary_reason = connection.execute(
            "SELECT reliability_excluded_reason FROM source_runs "
            "WHERE id = 'ordinary-failure'"
        ).fetchone()[0]

    assert excluded == {
        *(f"broad-{index}" for index in range(4)),
        *(f"probe-{index}" for index in range(3)),
    }
    assert ordinary_reason is None


def test_source_claim_is_atomic_due_aware_and_recovers_expired_lease(tmp_path):
    article_repo = SqliteArticleRepository(f"sqlite:///{tmp_path / 'claims.db'}")
    repository = SqliteSourceRepository(article_repo.engine)
    now = datetime(2026, 8, 2, 8, tzinfo=UTC)
    source = Source(
        id="official:claim-test",
        name="Claim Test",
        kind=SourceKind.RSS,
        tier=SourceTier.T1,
        publisher_group="official:claim-test",
        categories=(TopicCategory.AI_MODELS,),
        next_run_at=now - timedelta(minutes=1),
        created_at=now,
        updated_at=now,
    )
    repository.seed_many([source])

    def claim(owner: str):
        return owner, repository.claim_many(
            [source.id],
            now=now,
            lease_owner=owner,
            lease_expires_at=now + timedelta(minutes=15),
            due_only=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["task-a", "task-b"]))

    winners = [(owner, ids) for owner, ids in results if ids]
    assert len(winners) == 1
    winner = winners[0][0]
    loaded = repository.get(source.id)
    assert loaded is not None
    assert loaded.lease_owner == winner

    repository.release_many([source.id], lease_owner="wrong-owner")
    assert repository.get(source.id).lease_owner == winner  # type: ignore[union-attr]
    repository.release_many([source.id], lease_owner=winner)
    assert repository.get(source.id).lease_owner is None  # type: ignore[union-attr]

    repository.save(
        source.model_copy(
            update={
                "circuit_state": CircuitState.OPEN,
                "consecutive_failures": 3,
                "next_run_at": now + timedelta(minutes=5),
                "lease_owner": "crashed-task",
                "lease_expires_at": now - timedelta(seconds=1),
            }
        )
    )
    assert repository.claim_many(
        [source.id],
        now=now,
        lease_owner="recovery-task",
        lease_expires_at=now + timedelta(minutes=15),
        due_only=False,
    ) == []

    due_time = now + timedelta(minutes=5)
    assert repository.claim_many(
        [source.id],
        now=due_time,
        lease_owner="recovery-task",
        lease_expires_at=due_time + timedelta(minutes=15),
        due_only=True,
    ) == [source.id]
    recovered = repository.get(source.id)
    assert recovered is not None
    assert recovered.circuit_state is CircuitState.HALF_OPEN
    assert recovered.lease_owner == "recovery-task"
