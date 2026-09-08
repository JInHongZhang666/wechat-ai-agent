"""Application service for the stable, human-operated R1 label workbench."""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

from wechat_agent.domain.models import (
    ArticleRecord,
    CurationDecision,
    TopicCategory,
    public_category_slug,
)
from wechat_agent.domain.ports import ArticleRepository, CurationDecisionRepository
from wechat_agent.domain.relevance import is_distribution_eligible
from wechat_agent.evaluation.models import (
    BlindSelectionReviewItem,
    BlindSelectionReviewPage,
    DoubleReviewStatus,
    GoldLabel,
    LabelingCandidate,
    PredictionSnapshot,
    ReviewParticipant,
    ReviewerClaimRequest,
    SelectionAdjudicationDecision,
    SelectionConflictItem,
    SelectionConflictPage,
    SelectionReviewDecision,
    SelectionReviewItem,
    SelectionReviewPage,
    SelectionReviewStatus,
    WorkbenchItem,
    WorkbenchPage,
    WorkbenchStatus,
)
from wechat_agent.evaluation.review import (
    SELECTION_REVIEW_SET_ID,
    SELECTION_REVIEW_TARGET,
    select_selection_review_items,
)
from wechat_agent.storage.sqlite import SqliteEvaluationRepository


def candidate_from_article(
    article: ArticleRecord,
    decision: CurationDecision | None,
) -> LabelingCandidate:
    """Freeze the current suggestion separately from future human truth."""
    public_category = TopicCategory(public_category_slug(article.category))
    normalized_article = article.model_copy(
        update={"category": public_category}
    )
    return LabelingCandidate(
        evaluation_group=(
            f"{decision.strategy_version}:{decision.scored_at.isoformat()}"
            if decision is not None
            else f"unscored:{article.collected_at.isoformat()}"
        ),
        article=normalized_article,
        suggestion=PredictionSnapshot(
            relevant=(
                decision.eligible
                if decision is not None
                else is_distribution_eligible(article)
            ),
            category=public_category,
            selected=(decision.selected if decision is not None else False),
            distribution_score=(
                decision.distribution_score if decision is not None else None
            ),
            strategy_version=(
                decision.strategy_version if decision is not None else None
            ),
            reason=(decision.reason if decision is not None else None),
        ),
    )


class EvaluationWorkbenchService:
    """Create a deterministic stratified pool and record only complete labels."""

    def __init__(
        self,
        article_repository: ArticleRepository,
        decision_repository: CurationDecisionRepository,
        evaluation_repository: SqliteEvaluationRepository,
        *,
        target_count: int = 200,
    ) -> None:
        self._articles = article_repository
        self._decisions = decision_repository
        self._repository = evaluation_repository
        self.target_count = max(1, target_count)

    def ensure_candidates(self) -> int:
        """Top up without replacing already frozen or labelled candidates."""
        frozen = self._repository.list_items(limit=10000).items
        self._repository.remove_unlabelled_candidates(
            [
                item.candidate.article.id
                for item in frozen
                if item.label is None
                and not is_evaluation_eligible_article(item.candidate.article)
            ]
        )
        existing_ids = self._repository.candidate_ids()
        missing = max(0, self.target_count - len(existing_ids))
        if missing == 0:
            return 0
        articles = [
            article
            for article in self._articles.list_all(limit=10000)
            if article.id not in existing_ids
            and is_evaluation_eligible_article(article)
        ]
        decisions = self._decisions.get_many(
            [article.id for article in articles]
        )
        candidates = [
            candidate_from_article(article, decisions.get(article.id))
            for article in articles
        ]
        return self._repository.seed_candidates(
            _stratified_candidates(candidates, limit=missing)
        )

    def list_items(
        self,
        *,
        status: str,
        category: str | None,
        query: str | None,
        offset: int,
        limit: int,
    ) -> WorkbenchPage:
        return self._repository.list_items(
            status=status,
            category=category,
            query=query,
            offset=offset,
            limit=limit,
        )

    def save_label(self, article_id: str, label: GoldLabel) -> WorkbenchItem:
        if label.annotation_method != "human":
            raise ValueError("the human workbench accepts only human labels")
        return self._repository.save_label(article_id, label)

    def status(self) -> WorkbenchStatus:
        return self._repository.status(self.target_count)

    def ensure_selection_review(self) -> SelectionReviewStatus:
        frozen = self._repository.list_items(
            status="labelled",
            limit=10000,
        ).items
        review_items = select_selection_review_items(
            frozen,
            target_count=SELECTION_REVIEW_TARGET,
        )
        self._repository.seed_review_set(
            review_set_id=SELECTION_REVIEW_SET_ID,
            kind="selection",
            target_count=SELECTION_REVIEW_TARGET,
            criteria={
                "version": 1,
                "seed": SELECTION_REVIEW_SET_ID,
                "eligible": "human,relevant,selected",
                "per_category_cap": 8,
            },
            items=review_items,
        )
        return self.selection_review_status()

    def selection_review_status(self) -> SelectionReviewStatus:
        return self._repository.review_status(SELECTION_REVIEW_SET_ID)

    def list_selection_reviews(
        self,
        *,
        status: str,
        category: str | None,
        offset: int,
        limit: int,
    ) -> SelectionReviewPage:
        return self._repository.list_review_items(
            SELECTION_REVIEW_SET_ID,
            status=status,
            category=category,
            offset=offset,
            limit=limit,
        )

    def save_selection_review(
        self,
        article_id: str,
        decision: SelectionReviewDecision,
    ) -> SelectionReviewItem:
        if decision.annotation_method != "human":
            raise ValueError("the review workbench accepts only human decisions")
        return self._repository.save_selection_review(
            SELECTION_REVIEW_SET_ID,
            article_id,
            decision,
        )

    def double_review_status(self) -> DoubleReviewStatus:
        return self._repository.double_review_status(SELECTION_REVIEW_SET_ID)

    def claim_second_reviewer(
        self,
        claim: ReviewerClaimRequest,
    ) -> ReviewParticipant:
        return self._repository.claim_second_reviewer(
            SELECTION_REVIEW_SET_ID,
            claim,
        )

    def list_blind_selection_reviews(
        self,
        *,
        status: str,
        category: str | None,
        offset: int,
        limit: int,
    ) -> BlindSelectionReviewPage:
        return self._repository.list_blind_selection_reviews(
            SELECTION_REVIEW_SET_ID,
            status=status,
            category=category,
            offset=offset,
            limit=limit,
        )

    def save_second_selection_review(
        self,
        article_id: str,
        decision: SelectionReviewDecision,
    ) -> BlindSelectionReviewItem:
        if decision.annotation_method != "human":
            raise ValueError("the review workbench accepts only human decisions")
        return self._repository.save_second_selection_review(
            SELECTION_REVIEW_SET_ID,
            article_id,
            decision,
        )

    def list_selection_conflicts(
        self,
        *,
        status: str,
        offset: int,
        limit: int,
    ) -> SelectionConflictPage:
        return self._repository.list_selection_conflicts(
            SELECTION_REVIEW_SET_ID,
            status=status,
            offset=offset,
            limit=limit,
        )

    def save_selection_adjudication(
        self,
        article_id: str,
        decision: SelectionAdjudicationDecision,
    ) -> SelectionConflictItem:
        if decision.annotation_method != "human":
            raise ValueError(
                "the adjudication workbench accepts only human decisions"
            )
        return self._repository.save_selection_adjudication(
            SELECTION_REVIEW_SET_ID,
            article_id,
            decision,
        )

    def export_gold(self, output: Path | None = None) -> tuple[str, int]:
        samples = self._repository.list_gold_samples(human_only=True)
        body = "".join(sample.model_dump_json() + "\n" for sample in samples)
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(body, encoding="utf-8")
        return body, len(samples)


def _stratified_candidates(
    candidates: list[LabelingCandidate],
    *,
    limit: int,
) -> list[LabelingCandidate]:
    """Round-robin category/relevance/selection strata in a stable order."""
    buckets: dict[tuple[str, bool, bool], deque[LabelingCandidate]] = defaultdict(
        deque
    )
    for candidate in candidates:
        buckets[
            (
                candidate.article.category.value,
                candidate.suggestion.relevant,
                candidate.suggestion.selected,
            )
        ].append(candidate)
    keys = sorted(buckets)
    selected: list[LabelingCandidate] = []
    while len(selected) < limit and any(buckets.values()):
        for key in keys:
            if buckets[key] and len(selected) < limit:
                selected.append(buckets[key].popleft())
    return selected


def is_evaluation_eligible_article(article: ArticleRecord) -> bool:
    """Reject reserved/local test origins from the human gold candidate pool."""
    hostname = (urlparse(article.source_url).hostname or "").casefold()
    if not hostname:
        return False
    if hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
        "example.com",
        "example.org",
        "example.net",
    }:
        return False
    return not hostname.endswith(
        (".example", ".test", ".invalid", ".localhost")
    )
