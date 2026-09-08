"""Synchronous orchestration for the content pipeline.

Phase 4 adds two-pass AI screening and five-dimension scoring, with the
optional pre-screen step using an affordable model to filter noise before
the expensive scoring pass.

Design philosophy (from AIHOT V8+):
  "能用代码就别用模型" — curation decisions are code; models only score.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from enum import StrEnum
import logging
import re

from pydantic import BaseModel, Field

from wechat_agent.config import LlmConfig
from wechat_agent.domain.models import (
    ArticleRecord,
    CurationDecision,
    HeatScore,
    TopicCandidate,
    TopicCategory,
)
from wechat_agent.domain.fingerprints import article_content_fingerprint
from wechat_agent.domain.ports import ArticleRepository, CurationDecisionRepository
from wechat_agent.domain.relevance import is_distribution_eligible
from wechat_agent.scoring.curation import (
    explain_curation,
    get_category_limit,
    get_curation_priority,
    get_quality_adjustment,
    get_threshold_for,
    is_curated,
    select_curated,
)
from wechat_agent.scoring.heat import score_articles
from wechat_agent.scoring.classify_summarize import classify_and_summarize
from wechat_agent.scoring.pre_screen import pre_screen_sync
from wechat_agent.topics.select import select_topics

logger = logging.getLogger(__name__)

DISTRIBUTION_STRATEGY_VERSION = "distribution-v3.2"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    COLLECTING = "collecting"
    PRE_SCREENING = "pre_screening"
    RANKING = "ranking"
    REVIEW_READY = "review_ready"
    DRAFTING = "drafting"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineResult(BaseModel):
    saved_article_count: int = Field(ge=0)
    received_article_count: int = Field(ge=0, default=0)
    llm_candidate_count: int = Field(ge=0, default=0)
    skipped_unchanged_count: int = Field(ge=0, default=0)
    pre_screened_count: int = Field(ge=0, default=0)
    candidates: list[TopicCandidate]
    shortage: dict[TopicCategory, int] = Field(default_factory=dict)
    related_articles_by_topic: dict[str, list[ArticleRecord]]
    articles: list[ArticleRecord] = Field(default_factory=list)
    ingested_articles: list[ArticleRecord] = Field(default_factory=list)
    scores: dict[str, HeatScore] = Field(default_factory=dict)
    selected_article_ids: list[str] = Field(default_factory=list)


class ContentPipeline:
    """Persist normalized inputs, pre-screen, rank topics, and retain sources."""

    def __init__(
        self,
        repository: ArticleRepository,
        *,
        curation_repository: CurationDecisionRepository | None = None,
        llm_config: LlmConfig | None = None,
        enable_pre_screen: bool = True,
    ) -> None:
        self._repository = repository
        self._curation_repository = curation_repository
        self._llm_config = llm_config
        self._enable_pre_screen = enable_pre_screen

    def run(
        self,
        articles: Sequence[ArticleRecord],
        now,
    ) -> PipelineResult:
        article_list = list(articles)
        original_count = len(article_list)

        article_list = [
            article for article in article_list if is_distribution_eligible(article)
        ]

        # RSS/API sources repeatedly return the same recent window.  Resolve
        # durable identity and content changes before any paid model call.
        # A strategy-version change deliberately invalidates this cache so a
        # controlled replay can produce fresh curation decisions.
        eligible_by_url = {
            article.source_url: article.model_copy(
                update={
                    "ingestion_fingerprint": article_content_fingerprint(article),
                }
            )
            for article in article_list
        }
        changed = self._repository.filter_changed(tuple(eligible_by_url.values()))
        changed_urls = {article.source_url for article in changed}
        unchanged = [
            article
            for url, article in eligible_by_url.items()
            if url not in changed_urls
        ]
        if self._curation_repository is not None and unchanged:
            cached_decisions = self._curation_repository.get_many(
                [article.id for article in unchanged]
            )
            # A stored article without a decision was intentionally rejected
            # by pre-screening.  Only an existing stale decision requires a
            # controlled strategy replay.
            changed.extend(
                article
                for article in unchanged
                if (
                    article.id in cached_decisions
                    and cached_decisions[article.id].strategy_version
                    != DISTRIBUTION_STRATEGY_VERSION
                )
            )
        article_list = changed
        skipped_unchanged_count = len(eligible_by_url) - len(article_list)
        if skipped_unchanged_count:
            logger.info(
                "Incremental ingestion: skipped %d unchanged articles before LLM processing",
                skipped_unchanged_count,
            )

        # Persist the collector-input fingerprint before pre-screening.  This
        # remembers rejected items as already processed without exposing them
        # on public feeds, which are joined through curation decisions.
        ingested_articles = list(article_list)
        saved_article_count = self._repository.save_many(ingested_articles)
        llm_candidate_count = len(article_list)
        if not article_list:
            return PipelineResult(
                saved_article_count=saved_article_count,
                received_article_count=original_count,
                llm_candidate_count=0,
                skipped_unchanged_count=skipped_unchanged_count,
                pre_screened_count=0,
                candidates=[],
                related_articles_by_topic={},
                articles=[],
                ingested_articles=[],
            )

        # ── Phase 1: Pre-screen (optional, cheap model) ───────────────
        pre_screened_count = len(article_list)
        if self._enable_pre_screen and self._llm_config is not None:
            article_list = pre_screen_sync(article_list, self._llm_config)
            pre_screened_count = len(article_list)
            logger.info(
                "Pre-screen: %d/%d articles passed (%.0f%%)",
                pre_screened_count, llm_candidate_count,
                pre_screened_count / llm_candidate_count * 100
                if llm_candidate_count else 0,
            )

        # ── Phase 1.5: Classify + Summarize (cheap model) ──────────────
        if self._llm_config is not None and self._llm_config.is_available:
            article_list = classify_and_summarize(article_list, self._llm_config)
            logger.info("Classify+Summarize: %d articles processed", len(article_list))

        # ── Phase 2: Score with four independent dimensions ──────────
        # score_articles now supports optional LLM scoring; falls back to
        # deterministic tier-based scoring when no LLM config is present.
        scores = score_articles(article_list, now, llm_config=self._llm_config)

        # ── Phase 3: Make the code-owned curation decision ───────────
        selection = select_topics(
            article_list,
            now,
            per_category=5,
            scores=scores,
        )
        curated = select_curated(
            article_list,
            scores,
        )
        selected_article_ids = [article.id for article in curated]

        # AIHOT's "处理前置化": persist the normalized article and its complete
        # distribution assessment during ingestion, never during page render.
        # Replace accepted raw rows with enriched content while retaining the
        # original ingestion fingerprint.  This second upsert is not counted
        # as an additional ingestion.
        self._repository.save_many(article_list)
        self._save_curation_decisions(
            article_list,
            scores,
            set(selected_article_ids),
            now,
        )
        selected_article_ids = list(
            self._rebalance_recent_curation(article_list, scores, now)
        )

        # ── Phase 4: Related articles ─────────────────────────────────
        related = _related_articles(selection, article_list)

        return PipelineResult(
            saved_article_count=saved_article_count,
            received_article_count=original_count,
            llm_candidate_count=llm_candidate_count,
            skipped_unchanged_count=skipped_unchanged_count,
            pre_screened_count=pre_screened_count,
            candidates=list(selection),
            shortage=selection.shortage,
            related_articles_by_topic=related,
            articles=article_list,
            ingested_articles=ingested_articles,
            scores=scores,
            selected_article_ids=selected_article_ids,
        )

    def list_distribution_items(
        self,
        since,
        *,
        limit: int = 1000,
    ) -> list[tuple[ArticleRecord, CurationDecision]]:
        """Read the distribution feed from Article + CurationDecision only."""
        if self._curation_repository is None:
            return []
        articles = self._repository.list_recent(since, limit=limit)
        decisions = self._curation_repository.get_many(
            [article.id for article in articles]
        )
        return [
            (article, decisions[article.id])
            for article in articles
            if article.id in decisions
        ]

    def list_all_distribution_items(
        self,
        *,
        limit: int = 10000,
    ) -> list[tuple[ArticleRecord, CurationDecision]]:
        """Read the complete durable public pool for selected-set mirroring."""
        if self._curation_repository is None:
            return []
        articles = self._repository.list_all(limit=limit)
        decisions = self._curation_repository.get_many(
            [article.id for article in articles]
        )
        return [
            (article, decisions[article.id])
            for article in articles
            if article.id in decisions
        ]

    def backfill_legacy_result(self, result: PipelineResult) -> int:
        """One-way migration for task-era databases that predate decisions."""
        if self._curation_repository is None or not result.articles:
            return 0
        existing = self._curation_repository.get_many(
            [article.id for article in result.articles]
        )
        migration_articles = [
            article
            for article in result.articles
            if article.id not in existing
            or existing[article.id].strategy_version != DISTRIBUTION_STRATEGY_VERSION
            or existing[article.id].eligible != is_distribution_eligible(article)
        ]
        if not migration_articles:
            return 0
        scored_at = max(
            (article.collected_at for article in migration_articles),
            default=None,
        )
        if scored_at is None:
            return 0
        effective_scores = dict(result.scores)
        estimated_articles = [
            article
            for article in result.articles
            if result.scores.get(article.id) is not None
            and result.scores[article.id].confidence.value == "estimated"
        ]
        effective_scores.update(score_articles(estimated_articles, scored_at))
        eligible_articles = [
            article
            for article in result.articles
            if is_distribution_eligible(article)
        ]
        selected_ids = {
            article.id
            for article in select_curated(
                eligible_articles,
                effective_scores,
            )
        }
        decisions = self._build_curation_decisions(
            migration_articles,
            effective_scores,
            selected_ids,
            scored_at,
        )
        return self._curation_repository.save_many(decisions)

    def _save_curation_decisions(
        self,
        articles: list[ArticleRecord],
        scores: dict[str, HeatScore],
        selected_ids: set[str],
        scored_at,
    ) -> None:
        if self._curation_repository is None:
            return
        self._curation_repository.save_many(
            self._build_curation_decisions(
                articles,
                scores,
                selected_ids,
                scored_at,
            )
        )

    def _rebalance_recent_curation(
        self,
        current_articles: list[ArticleRecord],
        current_scores: dict[str, HeatScore],
        scored_at,
    ) -> set[str]:
        """Keep category capacity stable across incremental ingestion runs."""
        if self._curation_repository is None:
            return {
                article.id
                for article in select_curated(current_articles, current_scores)
            }

        recent_articles = self._repository.list_recent(
            scored_at - timedelta(days=7),
            limit=10_000,
        )
        existing = self._curation_repository.get_many(
            [article.id for article in recent_articles]
        )
        effective_scores = dict(current_scores)
        candidate_articles: list[ArticleRecord] = []
        for article in recent_articles:
            score = effective_scores.get(article.id)
            if score is None:
                decision = existing.get(article.id)
                if decision is None:
                    continue
                score = HeatScore(
                    value=decision.distribution_score,
                    confidence=decision.score_confidence,
                    components={
                        key: value
                        for key, value in decision.components.items()
                        if not key.startswith("curation_")
                    },
                )
                effective_scores[article.id] = score
            candidate_articles.append(article)

        globally_selected = {
            article.id
            for article in select_curated(candidate_articles, effective_scores)
        }
        self._curation_repository.save_many(
            self._build_curation_decisions(
                candidate_articles,
                effective_scores,
                globally_selected,
                scored_at,
            )
        )
        current_ids = {article.id for article in current_articles}
        return globally_selected & current_ids

    @staticmethod
    def _build_curation_decisions(
        articles: list[ArticleRecord],
        scores: dict[str, HeatScore],
        selected_ids: set[str],
        scored_at,
        *,
        strategy_version: str = DISTRIBUTION_STRATEGY_VERSION,
    ) -> list[CurationDecision]:
        decisions: list[CurationDecision] = []
        for article in articles:
            score = scores.get(article.id)
            if score is None:
                continue
            eligible = is_distribution_eligible(article)
            selected = eligible and article.id in selected_ids
            quality_adjustment = get_quality_adjustment(article)
            priority = get_curation_priority(article, score)
            components = {
                **score.components,
                "curation_quality_adjustment": quality_adjustment,
                "curation_priority": priority,
            }
            if not eligible:
                reason = "未通过 AI 相关性预筛，不进入公开分发池。"
            elif is_curated(article, score) and not selected:
                assessment = explain_curation(article, score).rsplit(
                    ", 结果:",
                    maxsplit=1,
                )[0]
                limit = get_category_limit(article.category)
                reason = (
                    f"{assessment}, 结果: 达到门槛但未进入该分类本轮 "
                    f"Top {limit}。"
                )
            else:
                reason = explain_curation(article, score)
            decisions.append(
                CurationDecision(
                    article_id=article.id,
                    distribution_score=score.value,
                    selected=selected,
                    eligible=eligible,
                    components=components,
                    threshold=get_threshold_for(article),
                    reason=reason,
                    score_confidence=score.confidence,
                    strategy_version=strategy_version,
                    scored_at=scored_at,
                )
            )
        return decisions


def _related_articles(
    candidates: Sequence[TopicCandidate],
    articles: Sequence[ArticleRecord],
) -> dict[str, list[ArticleRecord]]:
    """Map each candidate to exact input records in the same topic cluster."""
    by_id = {article.id: article for article in articles}
    mapping: dict[str, list[ArticleRecord]] = {}
    for candidate in candidates:
        origin = by_id[candidate.id]
        cluster_key = _cluster_key(origin)
        mapping[candidate.id] = [
            article
            for article in articles
            if article.id == origin.id or _cluster_key(article) == cluster_key
        ]
    return mapping


def _cluster_key(article: ArticleRecord) -> tuple[TopicCategory, str]:
    normalized_summary = re.sub(
        r"\s+", " ", article.summary.strip().casefold()
    )
    return article.category, normalized_summary
