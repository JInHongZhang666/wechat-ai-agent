from datetime import timedelta
import sqlite3

import pytest
from sqlalchemy.exc import IntegrityError

from tests.story_factories import NOW, make_candidate_pair, make_story_article
from wechat_agent.storage.sqlite import SqliteArticleRepository
from wechat_agent.storage.story_sqlite import SqliteStoryRepository
from wechat_agent.story.models import (
    Story,
    StoryArticleState,
    StoryPersistenceBatch,
    StoryReport,
    StoryReportRole,
    StoryRelation,
    StoryRelationType,
    StoryRevision,
    StoryShadowRun,
    StoryStatus,
    StoryTrigger,
)


@pytest.fixture
def story_repository(tmp_path):
    article_repository = SqliteArticleRepository(
        f"sqlite:///{tmp_path / 'story.db'}"
    )
    return SqliteStoryRepository(article_repository.engine)


@pytest.fixture
def story_batch():
    official = make_story_article(
        id="official",
        source_id="official:qwen",
        source_identity="official:qwen",
        source_type="official",
        tier="t1",
    )
    coverage = make_story_article(
        id="coverage",
        source_id="publisher:coverage",
        source_identity="publisher:coverage",
        title="阿里发布 Qwen 3.5 模型",
        published_at=NOW + timedelta(hours=2),
    )
    story = Story(
        id="story-internal-1",
        public_id=f"st_{'a' * 32}",
        status=StoryStatus.ACTIVE,
        primary_article_id=official.id,
        first_seen_at=official.published_at,
        latest_at=coverage.published_at,
        source_count=2,
        publisher_group_count=2,
        signal_count=2,
        report_count=2,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    reports = (
        StoryReport(
            story_id=story.id,
            article_id=official.id,
            role=StoryReportRole.PRIMARY,
            edge_score=1.0,
            attached_at=NOW,
            source_id=official.source_id,
            publisher_group="qwen-official",
        ),
        StoryReport(
            story_id=story.id,
            article_id=coverage.id,
            role=StoryReportRole.REPORT,
            edge_score=0.86,
            attached_at=NOW,
            source_id=coverage.source_id,
            publisher_group="publisher-coverage",
        ),
    )
    content_hash = StoryRevision.calculate_content_hash(
        member_article_ids=(official.id, coverage.id),
        primary_article_id=official.id,
        status=StoryStatus.ACTIVE,
        source_count=2,
        publisher_group_count=2,
        signal_count=2,
        report_count=2,
    )
    revision = StoryRevision(
        story_id=story.id,
        revision=1,
        member_article_ids=(official.id, coverage.id),
        primary_article_id=official.id,
        status=StoryStatus.ACTIVE,
        source_count=2,
        publisher_group_count=2,
        signal_count=2,
        report_count=2,
        trigger=StoryTrigger.CREATED,
        created_at=NOW,
        content_hash=content_hash,
    )
    run = StoryShadowRun(
        id="story-run-1",
        mode="incremental",
        status="success",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        input_article_count=2,
        processed_article_count=2,
        candidate_pair_count=1,
        same_count=1,
        created_story_count=1,
        created_revision_count=1,
        duration_ms=12,
        started_at=NOW,
        finished_at=NOW + timedelta(milliseconds=12),
    )
    return StoryPersistenceBatch(
        pairs=(make_candidate_pair(official, coverage, score=0.86),),
        stories=(story,),
        reports=reports,
        revisions=(revision,),
        article_states=(
            StoryArticleState(
                article_id=official.id,
                content_fingerprint="official-fingerprint",
                processed_at=NOW,
            ),
            StoryArticleState(
                article_id=coverage.id,
                content_fingerprint="coverage-fingerprint",
                processed_at=NOW,
            ),
        ),
        run=run,
    )


def test_schema_migration_18_creates_story_shadow_tables(tmp_path):
    article_repo = SqliteArticleRepository(f"sqlite:///{tmp_path / 'schema.db'}")

    with article_repo.engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = connection.exec_driver_sql(
            "SELECT name FROM schema_migrations WHERE version = 18"
        ).scalar_one()

    assert {
        "stories",
        "story_reports",
        "story_revisions",
        "story_relations",
        "story_candidate_pairs",
        "story_article_states",
        "story_shadow_runs",
    } <= tables
    assert version == "story_shadow_mode"


def test_story_repository_batch_is_idempotent(story_repository, story_batch):
    first = story_repository.apply_batch(story_batch)
    second = story_repository.apply_batch(story_batch)

    assert first.created_story_count == 1
    assert first.created_revision_count == 1
    assert second.created_story_count == 0
    assert second.updated_story_count == 0
    assert second.created_revision_count == 0


def test_story_repository_round_trips_details_pairs_and_status(
    story_repository, story_batch
):
    story_repository.apply_batch(story_batch)

    detail = story_repository.get_story("story-internal-1")
    pairs = story_repository.list_pairs(suggestion=None, offset=0, limit=20)
    status = story_repository.status()

    assert detail is not None
    assert detail.story.public_id == f"st_{'a' * 32}"
    assert {report.article_id for report in detail.reports} == {
        "official",
        "coverage",
    }
    assert detail.revisions[0].content_hash == story_batch.revisions[0].content_hash
    assert pairs.total == 1
    assert pairs.items == story_batch.pairs
    assert status.story_count == 1
    assert status.pair_count == 1
    assert status.latest_run == story_batch.run


def test_migration_18_preserves_v17_article_and_database_integrity(tmp_path):
    database_path = tmp_path / "upgrade.db"
    database_url = f"sqlite:///{database_path}"
    article = make_story_article(id="preserved")
    repository = SqliteArticleRepository(database_url)
    repository.save_many([article])

    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            DROP TABLE story_relations;
            DROP TABLE story_revisions;
            DROP TABLE story_reports;
            DROP TABLE story_candidate_pairs;
            DROP TABLE story_article_states;
            DROP TABLE story_shadow_runs;
            DROP TABLE stories;
            DELETE FROM schema_migrations WHERE version = 18;
            """
        )

    migrated = SqliteArticleRepository(database_url)

    assert migrated.list_all() == [article]
    with sqlite3.connect(database_path) as connection:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
    assert versions == list(range(1, 19))
    assert foreign_key_errors == []
    assert quick_check == "ok"


def test_story_batch_rolls_back_when_revision_identity_conflicts(
    story_repository, story_batch
):
    conflicting = story_batch.revisions[0].model_copy(
        update={"content_hash": "b" * 64}
    )
    invalid_batch = story_batch.model_copy(
        update={"revisions": (*story_batch.revisions, conflicting)}
    )

    with pytest.raises(IntegrityError):
        story_repository.apply_batch(invalid_batch)

    assert story_repository.list_stories(status=None, offset=0, limit=20).total == 0
    assert story_repository.latest_run() is None


def test_story_relation_is_persisted_in_the_same_batch(story_repository, story_batch):
    second_story = story_batch.stories[0].model_copy(
        update={
            "id": "story-internal-2",
            "public_id": f"st_{'b' * 32}",
        }
    )
    relation = StoryRelation(
        story_id=story_batch.stories[0].id,
        related_story_id=second_story.id,
        relation_type=StoryRelationType.RELATED,
        created_at=NOW,
    )
    batch = story_batch.model_copy(
        update={
            "stories": (*story_batch.stories, second_story),
            "relations": (relation,),
        }
    )

    story_repository.apply_batch(batch)

    with story_repository.engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT story_id, related_story_id, relation_type "
            "FROM story_relations"
        ).one()
    assert tuple(row) == (
        "story-internal-1",
        "story-internal-2",
        "related",
    )
