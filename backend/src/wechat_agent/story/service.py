"""Incremental, zero-token orchestration for Story shadow processing."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
import re
from threading import Lock
from time import perf_counter
from uuid import uuid4

from wechat_agent.domain.fingerprints import article_content_fingerprint
from wechat_agent.domain.models import (
    PUBLIC_TOPIC_CATEGORIES,
    ArticleRecord,
)
from wechat_agent.domain.ports import (
    ArticleRepository,
    CurationDecisionRepository,
    SourceRepository,
)
from wechat_agent.story.candidates import recall_candidate_pairs
from wechat_agent.story.clustering import (
    build_provisional_clusters,
    resolve_story_status,
)
from wechat_agent.story.models import (
    STORY_STRATEGY_VERSION,
    PairSuggestion,
    ProvisionalCluster,
    Story,
    StoryArticleState,
    StoryCandidatePair,
    StoryPersistenceBatch,
    StoryReport,
    StoryReportRole,
    StoryRevision,
    StoryShadowRun,
    StoryStatus,
    StoryTrigger,
)
from wechat_agent.story.ports import StoryRepository


_MAX_REPLAY_DAYS = 7
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]+"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)\S+"),
)


class StoryShadowService:
    """Run deterministic Story projection without affecting formal distribution."""

    def __init__(
        self,
        article_repository: ArticleRepository,
        curation_repository: CurationDecisionRepository,
        source_repository: SourceRepository,
        story_repository: StoryRepository,
    ) -> None:
        self._article_repository = article_repository
        self._curation_repository = curation_repository
        self._source_repository = source_repository
        self._story_repository = story_repository
        self._run_lock = Lock()

    def process(
        self,
        changed_articles: Sequence[ArticleRecord],
        now: datetime,
    ) -> StoryShadowRun:
        return self._execute(
            mode="incremental",
            days=_MAX_REPLAY_DAYS,
            now=now,
            changed_articles=tuple(changed_articles),
            force=False,
        )

    def replay(self, days: int, now: datetime) -> StoryShadowRun:
        if not 1 <= days <= _MAX_REPLAY_DAYS:
            raise ValueError("replay days must be between 1 and 7")
        return self._execute(
            mode="replay",
            days=days,
            now=now,
            changed_articles=(),
            force=True,
        )

    def _execute(
        self,
        *,
        mode: str,
        days: int,
        now: datetime,
        changed_articles: tuple[ArticleRecord, ...],
        force: bool,
    ) -> StoryShadowRun:
        if not self._run_lock.acquire(blocking=False):
            return self._persist_skipped_run(mode, now)
        started_clock = perf_counter()
        try:
            since = now - timedelta(days=days)
            corpus_by_id = {
                article.id: article
                for article in self._article_repository.list_recent(
                    since, limit=10000
                )
            }
            for article in changed_articles:
                if article.published_at >= since:
                    corpus_by_id[article.id] = article
            corpus = tuple(corpus_by_id.values())
            decisions = self._curation_repository.get_many(
                tuple(corpus_by_id)
            )
            eligible = tuple(
                sorted(
                    (
                        article
                        for article in corpus
                        if article.category in PUBLIC_TOPIC_CATEGORIES
                        and decisions.get(article.id) is not None
                        and decisions[article.id].eligible
                    ),
                    key=lambda article: (article.published_at, article.id),
                )
            )
            eligible_by_id = {article.id: article for article in eligible}
            input_articles = (
                eligible
                if force
                else tuple(
                    article
                    for article in changed_articles
                    if article.id in eligible_by_id
                )
            )
            window = self._story_repository.get_window(since)
            states = {
                (state.article_id, state.strategy_version): state
                for state in window.article_states
            }
            processed = tuple(
                article
                for article in input_articles
                if force
                or self._article_changed(
                    article,
                    states.get((article.id, STORY_STRATEGY_VERSION)),
                )
            )
            if not processed:
                run = self._run_model(
                    mode=mode,
                    status="success",
                    now=now,
                    window_start=since,
                    input_count=len(input_articles),
                    processed_count=0,
                    duration_ms=self._duration_ms(started_clock),
                )
                self._story_repository.apply_batch(
                    StoryPersistenceBatch(run=run)
                )
                return run

            new_pairs = recall_candidate_pairs(processed, eligible, now)
            all_pairs = self._merge_with_stored_pairs(new_pairs, eligible_by_id)
            sources = self._source_repository.list_all()
            publisher_groups = {
                source.id: source.publisher_group for source in sources
            }
            clusters = build_provisional_clusters(
                eligible,
                all_pairs,
                now,
                publisher_groups,
            )
            projection = self._reconcile(
                clusters=clusters,
                articles=eligible_by_id,
                pairs=all_pairs,
                current_stories=window.stories,
                current_reports=window.reports,
                publisher_groups=publisher_groups,
                now=now,
            )
            run = self._run_model(
                mode=mode,
                status="success",
                now=now,
                window_start=since,
                input_count=len(input_articles),
                processed_count=len(processed),
                candidate_pair_count=len(new_pairs),
                same_count=sum(
                    pair.suggestion is PairSuggestion.SAME for pair in new_pairs
                ),
                ambiguous_count=sum(
                    pair.suggestion is PairSuggestion.AMBIGUOUS
                    for pair in new_pairs
                ),
                hard_veto_count=sum(
                    pair.veto_reason is not None for pair in new_pairs
                ),
                created_story_count=projection.created_story_count,
                updated_story_count=projection.updated_story_count,
                created_revision_count=len(projection.revisions),
                duration_ms=self._duration_ms(started_clock),
            )
            batch = StoryPersistenceBatch(
                pairs=new_pairs,
                stories=projection.stories,
                reports=projection.reports,
                revisions=projection.revisions,
                relations=projection.relations,
                article_states=tuple(
                    StoryArticleState(
                        article_id=article.id,
                        content_fingerprint=article_content_fingerprint(article),
                        processed_at=now,
                    )
                    for article in processed
                ),
                run=run,
            )
            self._story_repository.apply_batch(batch)
            return run
        finally:
            self._run_lock.release()

    @staticmethod
    def _article_changed(
        article: ArticleRecord,
        state: StoryArticleState | None,
    ) -> bool:
        return state is None or state.content_fingerprint != article_content_fingerprint(
            article
        )

    def _merge_with_stored_pairs(
        self,
        new_pairs: tuple[StoryCandidatePair, ...],
        eligible_by_id: dict[str, ArticleRecord],
    ) -> tuple[StoryCandidatePair, ...]:
        pairs = {pair.pair_key: pair for pair in new_pairs}
        offset = 0
        while True:
            page = self._story_repository.list_pairs(
                suggestion=None,
                offset=offset,
                limit=100,
            )
            for pair in page.items:
                if (
                    pair.strategy_version == STORY_STRATEGY_VERSION
                    and pair.article_a_id in eligible_by_id
                    and pair.article_b_id in eligible_by_id
                ):
                    pairs.setdefault(pair.pair_key, pair)
            offset += len(page.items)
            if offset >= page.total or not page.items:
                break
        return tuple(
            sorted(pairs.values(), key=lambda pair: (-pair.score, pair.pair_key))
        )

    def _reconcile(
        self,
        *,
        clusters: tuple[ProvisionalCluster, ...],
        articles: dict[str, ArticleRecord],
        pairs: tuple[StoryCandidatePair, ...],
        current_stories: tuple[Story, ...],
        current_reports: tuple[StoryReport, ...],
        publisher_groups: dict[str, str],
        now: datetime,
    ) -> _Projection:
        members_by_story: dict[str, set[str]] = defaultdict(set)
        reports_by_story: dict[str, list[StoryReport]] = defaultdict(list)
        for report in current_reports:
            members_by_story[report.story_id].add(report.article_id)
            reports_by_story[report.story_id].append(report)
        overlaps: dict[int, list[tuple[int, Story]]] = {}
        for index, cluster in enumerate(clusters):
            cluster_ids = set(cluster.article_ids)
            overlaps[index] = sorted(
                (
                    (len(cluster_ids & members_by_story[story.id]), story)
                    for story in current_stories
                    if cluster_ids & members_by_story[story.id]
                ),
                key=lambda item: (-item[0], item[1].created_at, item[1].id),
            )

        cluster_indices = sorted(
            range(len(clusters)),
            key=lambda index: (
                0
                if any(
                    story.primary_article_id in clusters[index].article_ids
                    for _, story in overlaps[index]
                )
                else 1,
                clusters[index].first_seen_at,
                clusters[index].primary_article_id,
            ),
        )
        assigned_story_ids: set[str] = set()
        assignments: dict[int, Story | None] = {}
        for index in cluster_indices:
            assignments[index] = next(
                (
                    story
                    for _, story in overlaps[index]
                    if story.id not in assigned_story_ids
                ),
                None,
            )
            if assignments[index] is not None:
                assigned_story_ids.add(assignments[index].id)  # type: ignore[union-attr]

        desired_hits_by_story: dict[str, int] = defaultdict(int)
        for candidates in overlaps.values():
            for _, story in candidates:
                desired_hits_by_story[story.id] += 1

        stories: list[Story] = []
        reports: list[StoryReport] = []
        revisions: list[StoryRevision] = []
        relations = []
        created_story_count = 0
        updated_story_count = 0
        winner_by_cluster: dict[int, Story] = {}
        for index in range(len(clusters)):
            cluster = clusters[index]
            current = assignments[index]
            split = bool(overlaps[index]) and current is None
            if current is None:
                projected = Story(
                    id=f"story_{uuid4().hex}",
                    public_id=f"st_{uuid4().hex}",
                    status=resolve_story_status(cluster.latest_at, now),
                    primary_article_id=cluster.primary_article_id,
                    first_seen_at=cluster.first_seen_at,
                    latest_at=cluster.latest_at,
                    source_count=cluster.source_count,
                    publisher_group_count=cluster.publisher_group_count,
                    signal_count=cluster.signal_count,
                    report_count=len(cluster.article_ids),
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
                trigger = StoryTrigger.SPLIT if split else StoryTrigger.CREATED
                created_story_count += 1
                changed = True
            else:
                status = resolve_story_status(cluster.latest_at, now)
                desired_members = set(cluster.article_ids)
                current_members = members_by_story[current.id]
                changed = any(
                    (
                        desired_members != current_members,
                        cluster.primary_article_id != current.primary_article_id,
                        status is not current.status,
                        cluster.source_count != current.source_count,
                        cluster.publisher_group_count
                        != current.publisher_group_count,
                        cluster.signal_count != current.signal_count,
                        len(cluster.article_ids) != current.report_count,
                        current.merged_into_id is not None,
                    )
                )
                projected = current.model_copy(
                    update={
                        "status": status,
                        "primary_article_id": cluster.primary_article_id,
                        "first_seen_at": cluster.first_seen_at,
                        "latest_at": cluster.latest_at,
                        "source_count": cluster.source_count,
                        "publisher_group_count": cluster.publisher_group_count,
                        "signal_count": cluster.signal_count,
                        "report_count": len(cluster.article_ids),
                        "merged_into_id": None,
                        "revision": current.revision + (1 if changed else 0),
                        "updated_at": now if changed else current.updated_at,
                    }
                )
                trigger = self._change_trigger(
                    current,
                    projected,
                    desired_hits_by_story[current.id] > 1,
                    desired_members != current_members,
                )
                if changed:
                    updated_story_count += 1
            winner_by_cluster[index] = projected
            if changed:
                stories.append(projected)
                projected_reports = self._reports_for_cluster(
                    projected,
                    cluster,
                    articles,
                    pairs,
                    publisher_groups,
                    now,
                )
                reports.extend(projected_reports)
                revisions.append(
                    self._revision_for(
                        projected,
                        tuple(report.article_id for report in projected_reports),
                        trigger,
                        now,
                    )
                )

        for index, candidates in overlaps.items():
            winner = winner_by_cluster[index]
            for _, loser in candidates:
                if loser.id == winner.id or loser.id in assigned_story_ids:
                    continue
                merged = loser.model_copy(
                    update={
                        "status": StoryStatus.MERGED,
                        "merged_into_id": winner.id,
                        "revision": loser.revision + 1,
                        "updated_at": now,
                    }
                )
                stories.append(merged)
                reports.extend(reports_by_story[loser.id])
                revisions.append(
                    self._revision_for(
                        merged,
                        tuple(sorted(members_by_story[loser.id])),
                        StoryTrigger.MERGED,
                        now,
                    )
                )
                assigned_story_ids.add(loser.id)
                updated_story_count += 1

        return _Projection(
            stories=tuple(stories),
            reports=tuple(reports),
            revisions=tuple(revisions),
            relations=tuple(relations),
            created_story_count=created_story_count,
            updated_story_count=updated_story_count,
        )

    @staticmethod
    def _change_trigger(
        current: Story,
        projected: Story,
        split: bool,
        members_changed: bool,
    ) -> StoryTrigger:
        if split:
            return StoryTrigger.SPLIT
        if current.status is StoryStatus.SETTLED and projected.status is StoryStatus.ACTIVE:
            return StoryTrigger.REACTIVATED
        if members_changed:
            return StoryTrigger.REPORT_ATTACHED
        if current.primary_article_id != projected.primary_article_id:
            return StoryTrigger.PRIMARY_CHANGED
        if projected.status is StoryStatus.SETTLED:
            return StoryTrigger.SETTLED
        return StoryTrigger.REPORT_ATTACHED

    @staticmethod
    def _reports_for_cluster(
        story: Story,
        cluster: ProvisionalCluster,
        articles: dict[str, ArticleRecord],
        pairs: tuple[StoryCandidatePair, ...],
        publisher_groups: dict[str, str],
        now: datetime,
    ) -> tuple[StoryReport, ...]:
        pair_by_members = {
            frozenset((pair.article_a_id, pair.article_b_id)): pair
            for pair in pairs
        }
        result = []
        for article_id in cluster.article_ids:
            article = articles[article_id]
            edge = pair_by_members.get(
                frozenset((cluster.primary_article_id, article_id))
            )
            publisher_group = (
                publisher_groups.get(article.source_id, f"source:{article.source_id}")
                if article.source_id is not None
                else article.source_identity or f"article:{article.id}"
            )
            result.append(
                StoryReport(
                    story_id=story.id,
                    article_id=article.id,
                    role=(
                        StoryReportRole.PRIMARY
                        if article.id == cluster.primary_article_id
                        else StoryReportRole.REPORT
                    ),
                    edge_score=edge.score if edge is not None else None,
                    edge_features=edge.features if edge is not None else {},
                    edge_reasons=(edge.veto_reason,) if edge and edge.veto_reason else (),
                    attached_at=now,
                    source_id=article.source_id,
                    publisher_group=publisher_group,
                )
            )
        return tuple(result)

    @staticmethod
    def _revision_for(
        story: Story,
        member_ids: tuple[str, ...],
        trigger: StoryTrigger,
        now: datetime,
    ) -> StoryRevision:
        content_hash = StoryRevision.calculate_content_hash(
            member_article_ids=member_ids,
            primary_article_id=story.primary_article_id,
            status=story.status,
            source_count=story.source_count,
            publisher_group_count=story.publisher_group_count,
            signal_count=story.signal_count,
            report_count=story.report_count,
            strategy_version=story.strategy_version,
            merged_into_id=story.merged_into_id,
        )
        return StoryRevision(
            story_id=story.id,
            revision=story.revision,
            member_article_ids=tuple(sorted(member_ids)),
            primary_article_id=story.primary_article_id,
            status=story.status,
            source_count=story.source_count,
            publisher_group_count=story.publisher_group_count,
            signal_count=story.signal_count,
            report_count=story.report_count,
            merged_into_id=story.merged_into_id,
            trigger=trigger,
            created_at=now,
            content_hash=content_hash,
        )

    def record_failure(
        self,
        started_at: datetime,
        error: BaseException,
    ) -> StoryShadowRun:
        summary = f"{type(error).__name__}: {error}"
        for pattern in _SECRET_PATTERNS:
            summary = pattern.sub("[REDACTED]", summary)
        now = datetime.now(UTC)
        run = StoryShadowRun(
            id=f"story_run_{uuid4().hex}",
            mode="incremental",
            status="failed",
            window_start=started_at - timedelta(days=_MAX_REPLAY_DAYS),
            window_end=started_at,
            error_code="story_shadow_failure",
            error_summary=summary[:500],
            duration_ms=max(0, int((now - started_at).total_seconds() * 1000)),
            started_at=started_at,
            finished_at=now,
        )
        self._story_repository.save_failed_run(run)
        return run

    def _persist_skipped_run(self, mode: str, now: datetime) -> StoryShadowRun:
        run = self._run_model(
            mode=mode,
            status="skipped",
            now=now,
            window_start=now - timedelta(days=_MAX_REPLAY_DAYS),
            skip_reason="single_flight",
        )
        self._story_repository.apply_batch(StoryPersistenceBatch(run=run))
        return run

    @staticmethod
    def _duration_ms(started_clock: float) -> int:
        return max(0, int((perf_counter() - started_clock) * 1000))

    @staticmethod
    def _run_model(
        *,
        mode: str,
        status: str,
        now: datetime,
        window_start: datetime,
        input_count: int = 0,
        processed_count: int = 0,
        candidate_pair_count: int = 0,
        same_count: int = 0,
        ambiguous_count: int = 0,
        hard_veto_count: int = 0,
        created_story_count: int = 0,
        updated_story_count: int = 0,
        created_revision_count: int = 0,
        duration_ms: int = 0,
        skip_reason: str | None = None,
    ) -> StoryShadowRun:
        return StoryShadowRun(
            id=f"story_run_{uuid4().hex}",
            mode=mode,
            status=status,
            window_start=window_start,
            window_end=now,
            input_article_count=input_count,
            processed_article_count=processed_count,
            candidate_pair_count=candidate_pair_count,
            same_count=same_count,
            ambiguous_count=ambiguous_count,
            hard_veto_count=hard_veto_count,
            created_story_count=created_story_count,
            updated_story_count=updated_story_count,
            created_revision_count=created_revision_count,
            skip_reason=skip_reason,
            duration_ms=duration_ms,
            started_at=now,
            finished_at=now,
        )


class _Projection:
    def __init__(
        self,
        *,
        stories: tuple[Story, ...],
        reports: tuple[StoryReport, ...],
        revisions: tuple[StoryRevision, ...],
        relations: tuple,
        created_story_count: int,
        updated_story_count: int,
    ) -> None:
        self.stories = stories
        self.reports = reports
        self.revisions = revisions
        self.relations = relations
        self.created_story_count = created_story_count
        self.updated_story_count = updated_story_count
