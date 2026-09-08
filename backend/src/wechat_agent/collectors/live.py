"""Bounded two-window orchestration for all public collectors."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import uuid4

from wechat_agent.collectors.base import (
    CollectionResult,
    LiveCollectionResult,
    SourceCollector,
    failed_collection_result,
    merge_by_url,
)
from wechat_agent.domain.models import ArticleRecord, SourceRun, TopicCategory


class LiveRadarCollector:
    """Run public sources in parallel and expand only categories that are short."""

    def __init__(
        self,
        collectors: list[SourceCollector],
        *,
        target_per_category: int = 5,
        max_workers: int = 8,
    ) -> None:
        self.collectors = list(collectors)
        self.target_per_category = target_per_category
        self.max_workers = max_workers

    def collect(self, now: datetime) -> LiveCollectionResult:
        enabled_collectors = [
            collector
            for collector in self.collectors
            if getattr(collector, "enabled", True)
        ]
        return self._collect(enabled_collectors, now, expand_shortages=True)

    def collect_sources(
        self,
        now: datetime,
        source_ids: set[str] | frozenset[str],
        *,
        expand_shortages: bool,
    ) -> LiveCollectionResult:
        """Collect an explicitly claimed source set, ignoring stale local flags."""
        selected_collectors = [
            collector
            for collector in self.collectors
            if getattr(collector, "source_id", None) in source_ids
        ]
        return self._collect(
            selected_collectors,
            now,
            expand_shortages=expand_shortages,
        )

    def _collect(
        self,
        collectors: list[SourceCollector],
        now: datetime,
        *,
        expand_shortages: bool,
    ) -> LiveCollectionResult:
        initial_results = self._run(
            collectors,
            now - timedelta(days=7),
            now,
            window_days=7,
        )
        initial_articles = merge_by_url(
            [result.articles for _, result, _ in initial_results]
        )
        short_categories = self._short_categories(initial_articles)

        expanded_results: list[
            tuple[SourceCollector, CollectionResult, SourceRun]
        ] = []
        expanded_categories = short_categories if expand_shortages else set()
        if expanded_categories:
            expanded_collectors = [
                collector
                for collector in collectors
                if collector.categories & expanded_categories
            ]
            expanded_results = self._run(
                expanded_collectors,
                now - timedelta(days=30),
                now,
                window_days=30,
            )

        articles = merge_by_url(
            [
                initial_articles,
                *[
                    tuple(
                        article
                        for article in result.articles
                        if article.category in expanded_categories
                    )
                    for _, result, _ in expanded_results
                ],
            ]
        )
        all_results = [*initial_results, *expanded_results]
        warnings = tuple(
            dict.fromkeys(
                warning
                for _, result, _ in all_results
                for warning in result.warnings
            )
        )
        source_urls_by_collector: dict[str, set[str]] = defaultdict(set)
        for collector, result, _ in all_results:
            source_urls_by_collector[collector.name].update(
                article.source_url for article in result.articles
            )
        source_counts = {
            name: len(urls)
            for name, urls in sorted(source_urls_by_collector.items())
        }
        executed_expanded_categories = {
            category
            for collector, _, _ in expanded_results
            for category in collector.categories
            if category in expanded_categories
        }
        return LiveCollectionResult(
            articles=articles,
            warnings=warnings,
            window_days=30 if expanded_results else 7,
            expanded_categories=tuple(
                category
                for category in TopicCategory
                if category in executed_expanded_categories
            ),
            source_counts=source_counts,
            source_runs=tuple(run for _, _, run in all_results),
        )

    def _run(
        self,
        collectors: list[SourceCollector],
        since: datetime,
        now: datetime,
        *,
        window_days: int,
    ) -> list[tuple[SourceCollector, CollectionResult, SourceRun]]:
        if not collectors:
            return []
        results: list[tuple[SourceCollector, CollectionResult, SourceRun]] = []
        workers = min(max(self.max_workers, 1), len(collectors))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            starts: dict[object, tuple[datetime, float]] = {}
            for collector in collectors:
                started_at = datetime.now(UTC)
                future = executor.submit(collector.collect, since, now)
                futures[future] = collector
                starts[future] = (started_at, perf_counter())
            for future in as_completed(futures):
                collector = futures[future]
                started_at, started_counter = starts[future]
                try:
                    result = future.result()
                except Exception as error:
                    result = failed_collection_result(collector.name, error)
                finished_at = datetime.now(UTC)
                source_id = getattr(
                    collector,
                    "source_id",
                    f"collector:{collector.name.casefold()}",
                )
                run = SourceRun(
                    id=str(uuid4()),
                    source_id=source_id,
                    window_days=window_days,
                    started_at=started_at,
                    finished_at=finished_at,
                    status=result.status,
                    stage=result.stage,
                    http_status=result.http_status,
                    fetched_count=(
                        len(result.articles)
                        if result.fetched_count is None
                        else result.fetched_count
                    ),
                    stored_count=0,
                    skipped_count=result.skipped_count,
                    error_code=result.error_code,
                    error_summary=result.error_summary,
                    retry_after_seconds=result.retry_after_seconds,
                    duration_ms=max(
                        0,
                        round((perf_counter() - started_counter) * 1000),
                    ),
                )
                results.append((collector, result, run))
        return sorted(results, key=lambda item: item[0].name)

    def _short_categories(
        self, articles: tuple[ArticleRecord, ...]
    ) -> set[TopicCategory]:
        counts = Counter(article.category for article in articles)
        cats_in_articles = {a.category for a in articles}

        new_cats = {
            TopicCategory.AI_MODELS, TopicCategory.AI_PRODUCTS,
            TopicCategory.INDUSTRY, TopicCategory.PAPER, TopicCategory.TIP,
        }
        legacy_cats = {TopicCategory.TOOL, TopicCategory.NEWS}

        # Use new categories if any articles use them, otherwise legacy
        active = new_cats if (cats_in_articles & new_cats) else legacy_cats

        return {
            category
            for category in active
            if counts.get(category, 0) < self.target_per_category
        }
