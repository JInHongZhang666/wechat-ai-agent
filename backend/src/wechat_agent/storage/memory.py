"""In-memory article repository for when MongoDB is unavailable.

Provides a synchronous, in-process fallback that satisfies the
ArticleRepository protocol. Used when MongoDB is not running
or WECHAT_AGENT_MONGO_URI is not configured.
"""

from __future__ import annotations

from datetime import date, datetime
from threading import RLock

from wechat_agent.domain.fingerprints import article_content_fingerprint
from wechat_agent.domain.models import (
    AccountProfile,
    CircuitState,
    CurationDecision,
    CreatorProject,
    ContentOpportunity,
    CreationReplayReport,
    DailyReport,
    Source,
    SourceRun,
    LlmCallRecord,
    PromptVersion,
)


class InMemoryArticleRepository:
    """Store articles in a dict keyed by source_url. No persistence."""

    def __init__(self) -> None:
        self._articles: dict[str, dict] = {}

    def save_many(self, articles: list) -> int:
        for article in articles:
            self._articles[article.source_url] = article
        return len(articles)

    def filter_changed(self, articles: list) -> list:
        unique = {article.source_url: article for article in articles}
        return [
            article
            for url, article in unique.items()
            if url not in self._articles
            or article_content_fingerprint(self._articles[url])
            != article_content_fingerprint(article)
        ]

    def list_recent(self, since: datetime, limit: int = 100) -> list:
        matching = [
            a for a in self._articles.values()
            if a.published_at >= since
        ]
        matching.sort(key=lambda a: a.published_at, reverse=True)
        return matching[:limit]

    def list_all(self, limit: int = 10000) -> list:
        articles = sorted(
            self._articles.values(),
            key=lambda article: (article.published_at, article.id),
            reverse=True,
        )
        return articles[:limit]

class InMemoryCurationDecisionRepository:
    """Test/demo implementation with the same semantics as persistent stores."""

    def __init__(self) -> None:
        self._decisions: dict[str, CurationDecision] = {}

    def save_many(self, decisions: list[CurationDecision]) -> int:
        for decision in decisions:
            self._decisions[decision.article_id] = decision
        return len(decisions)

    def get_many(self, article_ids: list[str]) -> dict[str, CurationDecision]:
        return {
            article_id: self._decisions[article_id]
            for article_id in article_ids
            if article_id in self._decisions
        }


class InMemorySourceRepository:
    """Non-persistent registry used only by isolated tests and demos."""

    def __init__(self) -> None:
        self._sources: dict[str, Source] = {}
        self._lock = RLock()

    def seed_many(self, sources: list[Source]) -> int:
        with self._lock:
            inserted = 0
            for source in sources:
                existing = self._sources.get(source.id)
                if existing is None:
                    self._sources[source.id] = source
                    inserted += 1
                    continue
                self._sources[source.id] = existing.model_copy(
                    update={
                        "name": source.name,
                        "homepage": source.homepage,
                        "feed_url": source.feed_url,
                        "kind": source.kind,
                        "publisher_group": source.publisher_group,
                        "evidence_eligible": source.evidence_eligible,
                        "signal_only": source.signal_only,
                        "categories": source.categories,
                        "language": source.language,
                        "schedule_minutes": source.schedule_minutes,
                        "parser_version": source.parser_version,
                    }
                )
            return inserted

    def save(self, source: Source) -> None:
        with self._lock:
            self._sources[source.id] = source

    def get(self, source_id: str) -> Source | None:
        with self._lock:
            return self._sources.get(source_id)

    def list_all(self) -> list[Source]:
        with self._lock:
            return sorted(
                self._sources.values(),
                key=lambda source: (source.name, source.id),
            )

    def claim_many(
        self,
        source_ids,
        *,
        now,
        lease_owner,
        lease_expires_at,
        due_only,
        force_source_ids=(),
    ) -> list[str]:
        claimed: list[str] = []
        forced = set(force_source_ids)
        with self._lock:
            for source_id in sorted(set(source_ids)):
                source = self._sources.get(source_id)
                if source is None or not source.enabled:
                    continue
                lease_available = (
                    source.lease_expires_at is None
                    or source.lease_expires_at <= now
                )
                due = source.next_run_at is None or source.next_run_at <= now
                force_probe = source_id in forced
                circuit_allows = (
                    source.circuit_state is CircuitState.CLOSED
                    or due
                    or force_probe
                )
                if (
                    not lease_available
                    or not circuit_allows
                    or (due_only and not due and not force_probe)
                ):
                    continue
                self._sources[source_id] = source.model_copy(
                    update={
                        "lease_owner": lease_owner,
                        "lease_expires_at": lease_expires_at,
                        "circuit_state": (
                            CircuitState.HALF_OPEN
                            if source.circuit_state
                            in {CircuitState.OPEN, CircuitState.HALF_OPEN}
                            else source.circuit_state
                        ),
                        "updated_at": now,
                    }
                )
                claimed.append(source_id)
        return claimed

    def release_many(
        self,
        source_ids,
        *,
        lease_owner,
    ) -> None:
        with self._lock:
            for source_id in source_ids:
                source = self._sources.get(source_id)
                if source is None or source.lease_owner != lease_owner:
                    continue
                self._sources[source_id] = source.model_copy(
                    update={"lease_owner": None, "lease_expires_at": None}
                )


class InMemorySourceRunRepository:
    """In-memory source-run history with the same ordering as SQLite."""

    def __init__(self) -> None:
        self._runs: dict[str, SourceRun] = {}

    def save(self, run: SourceRun) -> None:
        self._runs[run.id] = run

    def list_recent(self, source_id, since, limit: int = 100) -> list[SourceRun]:
        matching = [
            run
            for run in self._runs.values()
            if run.source_id == source_id and run.started_at >= since
        ]
        matching.sort(key=lambda run: (run.started_at, run.id), reverse=True)
        return matching[:limit]


class InMemoryDailyReportRepository:
    """Thread-safe immutable report revisions for non-SQLite test adapters."""

    def __init__(self) -> None:
        self._reports: dict[tuple[date, int], DailyReport] = {}
        self._lock = RLock()

    def save(self, report: DailyReport) -> None:
        with self._lock:
            self._reports.setdefault((report.date, report.revision), report)

    def get(self, report_date: date) -> DailyReport | None:
        with self._lock:
            matching = [
                report
                for (stored_date, _), report in self._reports.items()
                if stored_date == report_date
            ]
            return max(matching, key=lambda report: report.revision, default=None)

    def latest(self) -> DailyReport | None:
        with self._lock:
            return max(
                self._reports.values(),
                key=lambda report: (report.date, report.revision),
                default=None,
            )

    def list_recent(self, limit: int = 30) -> list[DailyReport]:
        with self._lock:
            latest_by_date: dict[date, DailyReport] = {}
            for report in self._reports.values():
                current = latest_by_date.get(report.date)
                if current is None or report.revision > current.revision:
                    latest_by_date[report.date] = report
            return sorted(
                latest_by_date.values(),
                key=lambda report: (report.date, report.revision),
                reverse=True,
            )[:limit]


class InMemoryCreatorProjectRepository:
    """Thread-safe creator projects for non-persistent tests and demos."""

    def __init__(self) -> None:
        self._projects: dict[str, CreatorProject] = {}
        self._lock = RLock()

    def save(self, project: CreatorProject) -> None:
        with self._lock:
            self._projects[project.id] = project

    def get(self, project_id: str) -> CreatorProject | None:
        with self._lock:
            return self._projects.get(project_id)

    def get_by_topic(self, topic_id: str) -> CreatorProject | None:
        with self._lock:
            return next(
                (
                    project
                    for project in self._projects.values()
                    if project.topic_id == topic_id
                ),
                None,
            )

    def list_all(self) -> list[CreatorProject]:
        with self._lock:
            return sorted(
                self._projects.values(),
                key=lambda project: (project.updated_at, project.id),
                reverse=True,
            )


class InMemoryAccountProfileRepository:
    def __init__(self) -> None:
        self._profiles: dict[tuple[str, int], AccountProfile] = {}

    def save(self, profile: AccountProfile) -> None:
        self._profiles[(profile.id, profile.version)] = profile

    def get_current(self, profile_id: str = "local-default") -> AccountProfile | None:
        versions = self.list_versions(profile_id)
        return versions[0] if versions else None

    def list_versions(self, profile_id: str = "local-default") -> list[AccountProfile]:
        return sorted(
            (profile for (stored_id, _), profile in self._profiles.items() if stored_id == profile_id),
            key=lambda profile: profile.version,
            reverse=True,
        )


class InMemoryContentOpportunityRepository:
    def __init__(self) -> None:
        self._items: dict[str, ContentOpportunity] = {}

    def save(self, opportunity: ContentOpportunity) -> None:
        self._items[opportunity.id] = opportunity

    def get(self, opportunity_id: str) -> ContentOpportunity | None:
        return self._items.get(opportunity_id)

    def get_by_source(self, source_id: str) -> ContentOpportunity | None:
        return next((item for item in self._items.values() if item.source_id == source_id), None)

    def list_all(self) -> list[ContentOpportunity]:
        return sorted(
            self._items.values(),
            key=lambda item: (item.creation_score, item.created_at),
            reverse=True,
        )


class InMemoryGenerationAuditRepository:
    def __init__(self) -> None:
        self._prompts: dict[str, PromptVersion] = {}
        self._calls: dict[str, LlmCallRecord] = {}
        self._replays: dict[str, CreationReplayReport] = {}

    def save_prompt_version(self, prompt: PromptVersion) -> None:
        self._prompts[prompt.id] = prompt

    def list_prompt_versions(self) -> list[PromptVersion]:
        return sorted(self._prompts.values(), key=lambda item: (item.stage, item.version), reverse=True)

    def save_call(self, call: LlmCallRecord) -> None:
        self._calls[call.id] = call

    def list_calls(self, limit: int = 100) -> list[LlmCallRecord]:
        return sorted(self._calls.values(), key=lambda item: item.created_at, reverse=True)[:limit]

    def save_replay(self, report: CreationReplayReport) -> None:
        self._replays[report.id] = report

    def latest_replay(self) -> CreationReplayReport | None:
        values = self.list_replays(1)
        return values[0] if values else None

    def list_replays(self, limit: int = 20) -> list[CreationReplayReport]:
        return sorted(self._replays.values(), key=lambda item: item.created_at, reverse=True)[:limit]
