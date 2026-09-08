"""Transactional SQLite persistence for Story shadow state."""

from __future__ import annotations

from datetime import UTC, datetime
import json

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    delete,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.sqlite import insert

from wechat_agent.story.models import (
    STORY_STRATEGY_VERSION,
    PairPage,
    PairReviewState,
    PairSuggestion,
    Story,
    StoryApplyResult,
    StoryArticleState,
    StoryCandidatePair,
    StoryDetail,
    StoryPage,
    StoryPersistenceBatch,
    StoryReport,
    StoryRevision,
    StoryShadowRun,
    StoryShadowStatus,
    StoryStatus,
    StoryWindowState,
)


_STORY_METADATA = MetaData()

_STORIES = Table(
    "stories",
    _STORY_METADATA,
    Column("id", String, primary_key=True),
    Column("public_id", String, nullable=False, unique=True),
    Column("status", String, nullable=False),
    Column("primary_article_id", String, nullable=False),
    Column("first_seen_at", String, nullable=False),
    Column("latest_at", String, nullable=False),
    Column("source_count", Integer, nullable=False),
    Column("publisher_group_count", Integer, nullable=False),
    Column("signal_count", Integer, nullable=False),
    Column("report_count", Integer, nullable=False),
    Column("strategy_version", String, nullable=False),
    Column("merged_into_id", String, ForeignKey("stories.id"), nullable=True),
    Column("revision", Integer, nullable=False),
    Column("story_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
Index("ix_stories_status_latest", _STORIES.c.status, _STORIES.c.latest_at)

_STORY_REPORTS = Table(
    "story_reports",
    _STORY_METADATA,
    Column("story_id", String, ForeignKey("stories.id"), primary_key=True),
    Column("article_id", String, primary_key=True),
    Column("role", String, nullable=False),
    Column("source_id", String, nullable=True),
    Column("publisher_group", String, nullable=False),
    Column("attached_at", String, nullable=False),
    Column("strategy_version", String, nullable=False),
    Column("report_json", Text, nullable=False),
)
Index("ix_story_reports_article", _STORY_REPORTS.c.article_id)

_STORY_REVISIONS = Table(
    "story_revisions",
    _STORY_METADATA,
    Column("story_id", String, ForeignKey("stories.id"), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("content_hash", String, nullable=False),
    Column("trigger", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("revision_json", Text, nullable=False),
)
Index(
    "ux_story_revisions_content_hash",
    _STORY_REVISIONS.c.story_id,
    _STORY_REVISIONS.c.content_hash,
    unique=True,
)

_STORY_RELATIONS = Table(
    "story_relations",
    _STORY_METADATA,
    Column("story_id", String, ForeignKey("stories.id"), primary_key=True),
    Column(
        "related_story_id",
        String,
        ForeignKey("stories.id"),
        primary_key=True,
    ),
    Column("relation_type", String, primary_key=True),
    Column("relation_json", Text, nullable=False, default="{}"),
    Column("created_at", String, nullable=False),
)

_STORY_CANDIDATE_PAIRS = Table(
    "story_candidate_pairs",
    _STORY_METADATA,
    Column("pair_key", String, primary_key=True),
    Column("article_a_id", String, nullable=False),
    Column("article_b_id", String, nullable=False),
    Column("suggestion", String, nullable=False),
    Column("review_state", String, nullable=False),
    Column("veto_reason", String, nullable=True),
    Column("score", Float, nullable=False),
    Column("strategy_version", String, nullable=False),
    Column("generated_at", String, nullable=False),
    Column("pair_json", Text, nullable=False),
)
Index(
    "ix_story_pairs_suggestion_generated",
    _STORY_CANDIDATE_PAIRS.c.suggestion,
    _STORY_CANDIDATE_PAIRS.c.generated_at,
)

_STORY_ARTICLE_STATES = Table(
    "story_article_states",
    _STORY_METADATA,
    Column("article_id", String, primary_key=True),
    Column("strategy_version", String, primary_key=True),
    Column("content_fingerprint", String, nullable=False),
    Column("processed_at", String, nullable=False),
    Column("state_json", Text, nullable=False),
)
Index(
    "ix_story_article_states_strategy_processed",
    _STORY_ARTICLE_STATES.c.strategy_version,
    _STORY_ARTICLE_STATES.c.processed_at,
)

_STORY_SHADOW_RUNS = Table(
    "story_shadow_runs",
    _STORY_METADATA,
    Column("id", String, primary_key=True),
    Column("status", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("strategy_version", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("finished_at", String, nullable=False),
    Column("run_json", Text, nullable=False),
)
Index("ix_story_shadow_runs_started", _STORY_SHADOW_RUNS.c.started_at)


_STORY_TABLES = (
    _STORIES,
    _STORY_REPORTS,
    _STORY_REVISIONS,
    _STORY_RELATIONS,
    _STORY_CANDIDATE_PAIRS,
    _STORY_ARTICLE_STATES,
    _STORY_SHADOW_RUNS,
)


def create_story_shadow_schema(connection) -> None:
    """Create only Story shadow tables and indexes for migration 18."""
    for table in _STORY_TABLES:
        table.create(bind=connection, checkfirst=True)
    for table in _STORY_TABLES:
        for index in table.indexes:
            index.create(bind=connection, checkfirst=True)


def _canonical_json(model) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _story_values(story: Story) -> dict[str, object]:
    return {
        "id": story.id,
        "public_id": story.public_id,
        "status": story.status.value,
        "primary_article_id": story.primary_article_id,
        "first_seen_at": _utc_iso(story.first_seen_at),
        "latest_at": _utc_iso(story.latest_at),
        "source_count": story.source_count,
        "publisher_group_count": story.publisher_group_count,
        "signal_count": story.signal_count,
        "report_count": story.report_count,
        "strategy_version": story.strategy_version,
        "merged_into_id": story.merged_into_id,
        "revision": story.revision,
        "story_json": _canonical_json(story),
        "created_at": _utc_iso(story.created_at),
        "updated_at": _utc_iso(story.updated_at),
    }


def _run_values(run: StoryShadowRun) -> dict[str, object]:
    return {
        "id": run.id,
        "status": run.status,
        "mode": run.mode,
        "strategy_version": run.strategy_version,
        "started_at": _utc_iso(run.started_at),
        "finished_at": _utc_iso(run.finished_at),
        "run_json": _canonical_json(run),
    }


class SqliteStoryRepository:
    """Persist one complete Story projection batch in a single transaction."""

    def __init__(self, engine) -> None:
        self._engine = engine

    @property
    def engine(self):
        return self._engine

    def apply_batch(self, batch: StoryPersistenceBatch) -> StoryApplyResult:
        created_stories = 0
        updated_stories = 0
        created_revisions = 0
        with self._engine.begin() as connection:
            existing_stories: dict[str, str] = {}
            if batch.stories:
                existing_stories = {
                    row.id: row.story_json
                    for row in connection.execute(
                        select(_STORIES.c.id, _STORIES.c.story_json).where(
                            _STORIES.c.id.in_(tuple(story.id for story in batch.stories))
                        )
                    )
                }

            changed_story_ids: set[str] = set()
            for story in batch.stories:
                values = _story_values(story)
                previous = existing_stories.get(story.id)
                if previous is None:
                    created_stories += 1
                    changed_story_ids.add(story.id)
                elif previous != values["story_json"]:
                    updated_stories += 1
                    changed_story_ids.add(story.id)
                statement = insert(_STORIES).values(**values)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[_STORIES.c.id],
                        set_={
                            column.name: getattr(statement.excluded, column.name)
                            for column in _STORIES.c
                            if column.name not in {"id", "created_at"}
                        },
                    )
                )

            for pair in batch.pairs:
                values = {
                    "pair_key": pair.pair_key,
                    "article_a_id": pair.article_a_id,
                    "article_b_id": pair.article_b_id,
                    "suggestion": pair.suggestion.value,
                    "review_state": pair.review_state.value,
                    "veto_reason": pair.veto_reason,
                    "score": pair.score,
                    "strategy_version": pair.strategy_version,
                    "generated_at": _utc_iso(pair.generated_at),
                    "pair_json": _canonical_json(pair),
                }
                statement = insert(_STORY_CANDIDATE_PAIRS).values(**values)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[_STORY_CANDIDATE_PAIRS.c.pair_key],
                        set_={
                            column.name: getattr(statement.excluded, column.name)
                            for column in _STORY_CANDIDATE_PAIRS.c
                            if column.name != "pair_key"
                        },
                    )
                )

            if changed_story_ids:
                connection.execute(
                    delete(_STORY_REPORTS).where(
                        _STORY_REPORTS.c.story_id.in_(tuple(changed_story_ids))
                    )
                )
                for report in batch.reports:
                    if report.story_id not in changed_story_ids:
                        continue
                    connection.execute(
                        insert(_STORY_REPORTS).values(
                            story_id=report.story_id,
                            article_id=report.article_id,
                            role=report.role.value,
                            source_id=report.source_id,
                            publisher_group=report.publisher_group,
                            attached_at=_utc_iso(report.attached_at),
                            strategy_version=report.strategy_version,
                            report_json=_canonical_json(report),
                        )
                    )

            for revision in batch.revisions:
                values = {
                    "story_id": revision.story_id,
                    "revision": revision.revision,
                    "content_hash": revision.content_hash,
                    "trigger": revision.trigger.value,
                    "created_at": _utc_iso(revision.created_at),
                    "revision_json": _canonical_json(revision),
                }
                result = connection.execute(
                    insert(_STORY_REVISIONS)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=[
                            _STORY_REVISIONS.c.story_id,
                            _STORY_REVISIONS.c.content_hash,
                        ]
                    )
                )
                created_revisions += max(result.rowcount or 0, 0)

            for relation in batch.relations:
                values = {
                    "story_id": relation.story_id,
                    "related_story_id": relation.related_story_id,
                    "relation_type": relation.relation_type.value,
                    "relation_json": _canonical_json(relation),
                    "created_at": _utc_iso(relation.created_at),
                }
                statement = insert(_STORY_RELATIONS).values(**values)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            _STORY_RELATIONS.c.story_id,
                            _STORY_RELATIONS.c.related_story_id,
                            _STORY_RELATIONS.c.relation_type,
                        ],
                        set_={
                            "relation_json": statement.excluded.relation_json,
                            "created_at": statement.excluded.created_at,
                        },
                    )
                )

            for state in batch.article_states:
                values = {
                    "article_id": state.article_id,
                    "strategy_version": state.strategy_version,
                    "content_fingerprint": state.content_fingerprint,
                    "processed_at": _utc_iso(state.processed_at),
                    "state_json": _canonical_json(state),
                }
                statement = insert(_STORY_ARTICLE_STATES).values(**values)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            _STORY_ARTICLE_STATES.c.article_id,
                            _STORY_ARTICLE_STATES.c.strategy_version,
                        ],
                        set_={
                            "content_fingerprint": statement.excluded.content_fingerprint,
                            "processed_at": statement.excluded.processed_at,
                            "state_json": statement.excluded.state_json,
                        },
                    )
                )

            self._save_run(connection, batch.run)

        return StoryApplyResult(
            run=batch.run,
            created_story_count=created_stories,
            updated_story_count=updated_stories,
            created_revision_count=created_revisions,
        )

    def _save_run(self, connection, run: StoryShadowRun) -> None:
        values = _run_values(run)
        statement = insert(_STORY_SHADOW_RUNS).values(**values)
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[_STORY_SHADOW_RUNS.c.id],
                set_={
                    column.name: getattr(statement.excluded, column.name)
                    for column in _STORY_SHADOW_RUNS.c
                    if column.name != "id"
                },
            )
        )

    def save_failed_run(self, run: StoryShadowRun) -> None:
        with self._engine.begin() as connection:
            self._save_run(connection, run)

    def get_window(self, since: datetime) -> StoryWindowState:
        statement = select(_STORIES.c.story_json).where(
            _STORIES.c.latest_at >= _utc_iso(since)
        )
        with self._engine.connect() as connection:
            stories = tuple(
                Story.model_validate_json(value)
                for value in connection.execute(statement).scalars().all()
            )
            story_ids = tuple(story.id for story in stories)
            reports = ()
            if story_ids:
                reports = tuple(
                    StoryReport.model_validate_json(value)
                    for value in connection.execute(
                        select(_STORY_REPORTS.c.report_json).where(
                            _STORY_REPORTS.c.story_id.in_(story_ids)
                        )
                    ).scalars().all()
                )
            article_states = tuple(
                StoryArticleState.model_validate_json(value)
                for value in connection.execute(
                    select(_STORY_ARTICLE_STATES.c.state_json).where(
                        _STORY_ARTICLE_STATES.c.strategy_version
                        == STORY_STRATEGY_VERSION
                    )
                ).scalars().all()
            )
        return StoryWindowState(
            stories=stories,
            reports=reports,
            article_states=article_states,
        )

    def get_story(self, story_id: str) -> StoryDetail | None:
        with self._engine.connect() as connection:
            value = connection.execute(
                select(_STORIES.c.story_json).where(
                    or_(_STORIES.c.id == story_id, _STORIES.c.public_id == story_id)
                )
            ).scalar_one_or_none()
            if value is None:
                return None
            story = Story.model_validate_json(value)
            reports = tuple(
                StoryReport.model_validate_json(item)
                for item in connection.execute(
                    select(_STORY_REPORTS.c.report_json)
                    .where(_STORY_REPORTS.c.story_id == story.id)
                    .order_by(_STORY_REPORTS.c.article_id)
                ).scalars().all()
            )
            revisions = tuple(
                StoryRevision.model_validate_json(item)
                for item in connection.execute(
                    select(_STORY_REVISIONS.c.revision_json)
                    .where(_STORY_REVISIONS.c.story_id == story.id)
                    .order_by(_STORY_REVISIONS.c.revision)
                ).scalars().all()
            )
            article_ids = tuple(report.article_id for report in reports)
            pairs = ()
            if article_ids:
                pairs = tuple(
                    StoryCandidatePair.model_validate_json(item)
                    for item in connection.execute(
                        select(_STORY_CANDIDATE_PAIRS.c.pair_json)
                        .where(
                            _STORY_CANDIDATE_PAIRS.c.article_a_id.in_(article_ids),
                            _STORY_CANDIDATE_PAIRS.c.article_b_id.in_(article_ids),
                        )
                        .order_by(_STORY_CANDIDATE_PAIRS.c.pair_key)
                    ).scalars().all()
                )
        return StoryDetail(
            story=story,
            reports=reports,
            revisions=revisions,
            pairs=pairs,
        )

    def list_stories(
        self,
        *,
        status: StoryStatus | None,
        offset: int,
        limit: int,
    ) -> StoryPage:
        predicate = _STORIES.c.status == status.value if status is not None else None
        count_statement = select(func.count()).select_from(_STORIES)
        items_statement = select(_STORIES.c.story_json)
        if predicate is not None:
            count_statement = count_statement.where(predicate)
            items_statement = items_statement.where(predicate)
        items_statement = (
            items_statement.order_by(_STORIES.c.latest_at.desc(), _STORIES.c.id)
            .offset(offset)
            .limit(limit)
        )
        with self._engine.connect() as connection:
            total = int(connection.execute(count_statement).scalar_one())
            items = tuple(
                Story.model_validate_json(value)
                for value in connection.execute(items_statement).scalars().all()
            )
        return StoryPage(items=items, total=total, offset=offset, limit=limit)

    def list_pairs(
        self,
        *,
        suggestion: PairSuggestion | None,
        offset: int,
        limit: int,
    ) -> PairPage:
        predicate = (
            _STORY_CANDIDATE_PAIRS.c.suggestion == suggestion.value
            if suggestion is not None
            else None
        )
        count_statement = select(func.count()).select_from(_STORY_CANDIDATE_PAIRS)
        items_statement = select(_STORY_CANDIDATE_PAIRS.c.pair_json)
        if predicate is not None:
            count_statement = count_statement.where(predicate)
            items_statement = items_statement.where(predicate)
        items_statement = (
            items_statement.order_by(
                _STORY_CANDIDATE_PAIRS.c.generated_at.desc(),
                _STORY_CANDIDATE_PAIRS.c.pair_key,
            )
            .offset(offset)
            .limit(limit)
        )
        with self._engine.connect() as connection:
            total = int(connection.execute(count_statement).scalar_one())
            items = tuple(
                StoryCandidatePair.model_validate_json(value)
                for value in connection.execute(items_statement).scalars().all()
            )
        return PairPage(items=items, total=total, offset=offset, limit=limit)

    def latest_run(self) -> StoryShadowRun | None:
        statement = (
            select(_STORY_SHADOW_RUNS.c.run_json)
            .order_by(
                _STORY_SHADOW_RUNS.c.started_at.desc(),
                _STORY_SHADOW_RUNS.c.id.desc(),
            )
            .limit(1)
        )
        with self._engine.connect() as connection:
            value = connection.execute(statement).scalar_one_or_none()
        return StoryShadowRun.model_validate_json(value) if value else None

    def list_runs(
        self,
        *,
        since: datetime,
        offset: int,
        limit: int,
    ) -> tuple[StoryShadowRun, ...]:
        statement = (
            select(_STORY_SHADOW_RUNS.c.run_json)
            .where(_STORY_SHADOW_RUNS.c.started_at >= _utc_iso(since))
            .order_by(_STORY_SHADOW_RUNS.c.started_at, _STORY_SHADOW_RUNS.c.id)
            .offset(offset)
            .limit(limit)
        )
        with self._engine.connect() as connection:
            return tuple(
                StoryShadowRun.model_validate_json(value)
                for value in connection.execute(statement).scalars().all()
            )

    def list_revisions(
        self,
        *,
        since: datetime,
        offset: int,
        limit: int,
    ) -> tuple[StoryRevision, ...]:
        statement = (
            select(_STORY_REVISIONS.c.revision_json)
            .where(_STORY_REVISIONS.c.created_at >= _utc_iso(since))
            .order_by(_STORY_REVISIONS.c.created_at, _STORY_REVISIONS.c.story_id)
            .offset(offset)
            .limit(limit)
        )
        with self._engine.connect() as connection:
            return tuple(
                StoryRevision.model_validate_json(value)
                for value in connection.execute(statement).scalars().all()
            )

    def status(self) -> StoryShadowStatus:
        with self._engine.connect() as connection:
            story_count = int(
                connection.execute(
                    select(func.count()).select_from(_STORIES)
                ).scalar_one()
            )
            active_count = int(
                connection.execute(
                    select(func.count())
                    .select_from(_STORIES)
                    .where(_STORIES.c.status == StoryStatus.ACTIVE.value)
                ).scalar_one()
            )
            pair_count = int(
                connection.execute(
                    select(func.count()).select_from(_STORY_CANDIDATE_PAIRS)
                ).scalar_one()
            )
            pending_count = int(
                connection.execute(
                    select(func.count())
                    .select_from(_STORY_CANDIDATE_PAIRS)
                    .where(
                        _STORY_CANDIDATE_PAIRS.c.review_state
                        == PairReviewState.UNREVIEWED.value
                    )
                ).scalar_one()
            )
        return StoryShadowStatus(
            strategy_version=STORY_STRATEGY_VERSION,
            latest_run=self.latest_run(),
            story_count=story_count,
            active_story_count=active_count,
            pair_count=pair_count,
            pending_pair_count=pending_count,
        )
