from datetime import UTC, datetime
import json

import pytest
from sqlalchemy import inspect

from wechat_agent.domain.models import ArticleRecord, TopicCategory
from wechat_agent.evaluation.models import (
    GoldLabel,
    LabelingCandidate,
    PredictionSnapshot,
    ReviewerClaimRequest,
    SelectionAdjudicationDecision,
    SelectionReviewDecision,
)
from wechat_agent.evaluation.review import (
    AdjudicationAlreadyExistsError,
    AdjudicationNotRequiredError,
    AdjudicatorNotIndependentError,
    FirstReviewerAlreadyClaimedError,
    ReviewSubmissionAlreadyExistsError,
    ReviewerNotIndependentError,
    SecondReviewIncompleteError,
    SecondReviewerAlreadyClaimedError,
)
from wechat_agent.storage.sqlite import (
    SqliteArticleRepository,
    SqliteEvaluationRepository,
)


NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _seed_review_set(
    repository: SqliteEvaluationRepository,
    *,
    count: int = 1,
    initial_annotator: str = "initial-reviewer",
):
    candidates = []
    for position in range(1, count + 1):
        article_id = f"double-review-{position}"
        article = ArticleRecord(
            id=article_id,
            source_id=f"source:{article_id}",
            title=f"Double review {position}",
            source_name="Review Source",
            source_url=f"https://review-source.ai/{article_id}",
            published_at=NOW,
            collected_at=NOW,
            summary="Double review fixture",
            category=(
                TopicCategory.PAPER
                if position % 2
                else TopicCategory.INDUSTRY
            ),
            source_identity=f"source:{article_id}",
            tier="t1" if position == 1 else "t2",
        )
        candidates.append(
            LabelingCandidate(
                evaluation_group="double-review-fixture",
                article=article,
                suggestion=PredictionSnapshot(
                    relevant=True,
                    category=article.category,
                    selected=True,
                    distribution_score=88,
                    strategy_version="distribution-v3",
                ),
            )
        )
    repository.seed_candidates(candidates)
    for candidate in candidates:
        repository.save_label(
            candidate.article.id,
            GoldLabel(
                relevant=True,
                category=candidate.article.category,
                selected=True,
                primary_source_correct=True,
                story_key=None,
                creation_worthy=True,
                evidence_sufficient=True,
                annotation_method="human",
                data_license="public-source-metadata-only",
                annotator=initial_annotator,
                annotated_at=NOW,
                notes="Initial label.",
            ),
        )
    items = repository.list_items(status="labelled", limit=100).items
    repository.seed_review_set(
        review_set_id="selection-audit-v1",
        kind="selection",
        target_count=40,
        criteria={"seed": "selection-audit-v1"},
        items=items,
    )
    return items


def _first_review(
    *,
    reviewer: str = "reviewer-a",
    selected: bool = True,
    reason_code: str = "wechat_value",
) -> SelectionReviewDecision:
    return SelectionReviewDecision(
        selected=selected,
        reason_code=reason_code,
        reviewer=reviewer,
        reviewed_at=NOW,
        annotation_method="human",
    )


def _claimed_repository(tmp_path, *, count: int = 2):
    article_repository = SqliteArticleRepository(
        f"sqlite:///{tmp_path / 'claimed-review.db'}"
    )
    repository = SqliteEvaluationRepository(article_repository.engine)
    items = _seed_review_set(repository, count=count)
    for item in items:
        repository.save_selection_review(
            "selection-audit-v1",
            item.candidate.article.id,
            _first_review(),
        )
    repository.claim_second_reviewer(
        "selection-audit-v1",
        ReviewerClaimRequest(reviewer="reviewer-b", claimed_at=NOW),
    )
    return repository, items


def _second_review(
    *,
    reviewer: str = "reviewer-b",
    selected: bool,
    reason_code: str,
) -> SelectionReviewDecision:
    return SelectionReviewDecision(
        selected=selected,
        reason_code=reason_code,
        reviewer=reviewer,
        reviewed_at=NOW,
        annotation_method="human",
    )


def _adjudication(
    *,
    adjudicator: str = "reviewer-a",
    selected: bool,
    reason_code: str,
    notes: str = "Resolved after comparing both reviews.",
) -> SelectionAdjudicationDecision:
    return SelectionAdjudicationDecision(
        selected=selected,
        reason_code=reason_code,
        notes=notes,
        adjudicator=adjudicator,
        adjudicated_at=NOW,
        annotation_method="human",
    )


def _completed_second_review_with_one_conflict(tmp_path):
    repository, items = _claimed_repository(tmp_path)
    repository.save_second_selection_review(
        "selection-audit-v1",
        items[0].candidate.article.id,
        _second_review(selected=True, reason_code="wechat_value"),
    )
    repository.save_second_selection_review(
        "selection-audit-v1",
        items[1].candidate.article.id,
        _second_review(selected=False, reason_code="too_niche"),
    )
    return repository, items


def test_sqlite_schema_creates_double_review_tables_idempotently(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'double-review.db'}"
    repository = SqliteArticleRepository(database_url)
    SqliteArticleRepository(database_url)

    with repository.engine.connect() as connection:
        versions = connection.exec_driver_sql(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).scalars().all()
        tables = set(inspect(connection).get_table_names())

    assert versions[-1] == 18
    assert {
        "evaluation_review_participants",
        "evaluation_review_submissions",
        "evaluation_review_adjudications",
    } <= tables


def test_completed_v11_review_is_backfilled_as_reviewer_a(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'v11-review.db'}"
    article_repository = SqliteArticleRepository(database_url)
    repository = SqliteEvaluationRepository(article_repository.engine)
    items = _seed_review_set(repository)
    repository.save_selection_review(
        "selection-audit-v1",
        items[0].candidate.article.id,
        _first_review(),
    )
    with article_repository.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE evaluation_review_adjudications")
        connection.exec_driver_sql("DROP TABLE evaluation_review_submissions")
        connection.exec_driver_sql("DROP TABLE evaluation_review_participants")
        connection.exec_driver_sql(
            "DELETE FROM schema_migrations WHERE version = 12"
        )

    migrated = SqliteArticleRepository(database_url)
    with migrated.engine.connect() as connection:
        participants = connection.exec_driver_sql(
            "SELECT role, reviewer FROM evaluation_review_participants"
        ).all()
        submissions = connection.exec_driver_sql(
            "SELECT reviewer_role, reviewer FROM evaluation_review_submissions"
        ).all()

    assert participants == [("reviewer_a", "reviewer-a")]
    assert submissions == [("reviewer_a", "reviewer-a")]


def test_new_first_review_dual_writes_reviewer_a(tmp_path):
    article_repository = SqliteArticleRepository(
        f"sqlite:///{tmp_path / 'dual-write.db'}"
    )
    repository = SqliteEvaluationRepository(article_repository.engine)
    items = _seed_review_set(repository)
    repository.save_selection_review(
        "selection-audit-v1",
        items[0].candidate.article.id,
        _first_review(),
    )

    with article_repository.engine.connect() as connection:
        participants = connection.exec_driver_sql(
            "SELECT role, reviewer FROM evaluation_review_participants"
        ).all()
        submissions = connection.exec_driver_sql(
            "SELECT reviewer_role, reviewer FROM evaluation_review_submissions"
        ).all()

    assert participants == [("reviewer_a", "reviewer-a")]
    assert submissions == [("reviewer_a", "reviewer-a")]


def test_first_reviewer_binding_rejects_a_different_human(tmp_path):
    article_repository = SqliteArticleRepository(
        f"sqlite:///{tmp_path / 'first-binding.db'}"
    )
    repository = SqliteEvaluationRepository(article_repository.engine)
    items = _seed_review_set(repository, count=2)
    repository.save_selection_review(
        "selection-audit-v1",
        items[0].candidate.article.id,
        _first_review(),
    )

    with pytest.raises(FirstReviewerAlreadyClaimedError):
        repository.save_selection_review(
            "selection-audit-v1",
            items[1].candidate.article.id,
            _first_review(reviewer="reviewer-c"),
        )


def test_second_reviewer_claim_rejects_initial_or_first_reviewer(tmp_path):
    article_repository = SqliteArticleRepository(
        f"sqlite:///{tmp_path / 'independence.db'}"
    )
    repository = SqliteEvaluationRepository(article_repository.engine)
    items = _seed_review_set(repository)
    repository.save_selection_review(
        "selection-audit-v1",
        items[0].candidate.article.id,
        _first_review(),
    )

    for reviewer in (" INITIAL-REVIEWER ", "  REVIEWER-A "):
        with pytest.raises(ReviewerNotIndependentError):
            repository.claim_second_reviewer(
                "selection-audit-v1",
                ReviewerClaimRequest(reviewer=reviewer, claimed_at=NOW),
            )


def test_second_reviewer_claim_is_immutable_and_updates_status(tmp_path):
    article_repository = SqliteArticleRepository(
        f"sqlite:///{tmp_path / 'second-binding.db'}"
    )
    repository = SqliteEvaluationRepository(article_repository.engine)
    items = _seed_review_set(repository)
    repository.save_selection_review(
        "selection-audit-v1",
        items[0].candidate.article.id,
        _first_review(),
    )
    before = repository.double_review_status("selection-audit-v1")
    assert before.lifecycle == "awaiting_second_reviewer"

    claimed = repository.claim_second_reviewer(
        "selection-audit-v1",
        ReviewerClaimRequest(reviewer="reviewer-b", claimed_at=NOW),
    )
    repeated = repository.claim_second_reviewer(
        "selection-audit-v1",
        ReviewerClaimRequest(reviewer=" ＲＥＶＩＥＷＥＲ－Ｂ ", claimed_at=NOW),
    )
    assert claimed.role == "reviewer_b"
    assert repeated == claimed
    status = repository.double_review_status("selection-audit-v1")
    assert status.lifecycle == "second_reviewing"
    assert status.second_reviewer == "reviewer-b"
    assert status.second_pending_count == 1
    with pytest.raises(SecondReviewerAlreadyClaimedError):
        repository.claim_second_reviewer(
            "selection-audit-v1",
            ReviewerClaimRequest(reviewer="reviewer-c", claimed_at=NOW),
        )


def test_blind_list_contains_source_but_no_initial_or_strategy_decisions(tmp_path):
    repository, _ = _claimed_repository(tmp_path)

    page = repository.list_blind_selection_reviews(
        "selection-audit-v1",
        status="pending",
        category=None,
        offset=0,
        limit=100,
    )
    dumped = page.model_dump(mode="json")
    assert page.total == 2
    assert dumped["items"][0]["article"]["source_url"].startswith("https://")
    serialized = json.dumps(dumped)
    for forbidden in (
        "initial_label",
        "reviewer_a",
        "suggestion",
        "distribution_score",
        "strategy_version",
    ):
        assert forbidden not in serialized


def test_second_review_is_immutable_and_does_not_change_gold(tmp_path):
    repository, items = _claimed_repository(tmp_path)
    article_id = items[0].candidate.article.id
    before = {
        sample.article.id: sample.label for sample in repository.list_gold_samples()
    }[article_id]

    saved = repository.save_second_selection_review(
        "selection-audit-v1",
        article_id,
        _second_review(selected=False, reason_code="too_niche"),
    )

    after = {
        sample.article.id: sample.label for sample in repository.list_gold_samples()
    }[article_id]
    assert saved.review is not None
    assert saved.review.selected is False
    assert after == before
    with pytest.raises(ReviewSubmissionAlreadyExistsError):
        repository.save_second_selection_review(
            "selection-audit-v1",
            article_id,
            _second_review(selected=True, reason_code="wechat_value"),
        )


def test_second_review_requires_claimed_identity(tmp_path):
    repository, items = _claimed_repository(tmp_path)
    with pytest.raises(ReviewerNotIndependentError):
        repository.save_second_selection_review(
            "selection-audit-v1",
            items[0].candidate.article.id,
            _second_review(
                reviewer="reviewer-c",
                selected=True,
                reason_code="wechat_value",
            ),
        )


def test_second_review_status_counts_consensus_and_conflict(tmp_path):
    repository, items = _claimed_repository(tmp_path)
    repository.save_second_selection_review(
        "selection-audit-v1",
        items[0].candidate.article.id,
        _second_review(selected=True, reason_code="wechat_value"),
    )
    repository.save_second_selection_review(
        "selection-audit-v1",
        items[1].candidate.article.id,
        _second_review(selected=False, reason_code="too_niche"),
    )

    status = repository.double_review_status("selection-audit-v1")
    assert status.lifecycle == "awaiting_adjudication"
    assert status.second_completed_count == 2
    assert status.consensus_count == 1
    assert status.conflict_count == 1
    assert status.pending_adjudication_count == 1


def test_second_review_without_conflicts_completes_lifecycle(tmp_path):
    repository, items = _claimed_repository(tmp_path)
    for item in items:
        repository.save_second_selection_review(
            "selection-audit-v1",
            item.candidate.article.id,
            _second_review(selected=True, reason_code="wechat_value"),
        )

    status = repository.double_review_status("selection-audit-v1")
    assert status.lifecycle == "complete"
    assert status.consensus_count == 2
    assert status.conflict_count == 0


def test_conflicts_are_locked_until_second_reviewer_finishes(tmp_path):
    repository, items = _claimed_repository(tmp_path)
    repository.save_second_selection_review(
        "selection-audit-v1",
        items[0].candidate.article.id,
        _second_review(selected=True, reason_code="wechat_value"),
    )

    with pytest.raises(SecondReviewIncompleteError):
        repository.list_selection_conflicts(
            "selection-audit-v1",
            status="pending",
            offset=0,
            limit=100,
        )


def test_conflict_list_exposes_both_answers_after_b_finishes(tmp_path):
    repository, _ = _completed_second_review_with_one_conflict(tmp_path)

    page = repository.list_selection_conflicts(
        "selection-audit-v1",
        status="pending",
        offset=0,
        limit=100,
    )

    assert page.total == 1
    assert page.items[0].reviewer_a.selected is True
    assert page.items[0].reviewer_b.selected is False


def test_adjudication_updates_only_selected_and_notes_and_is_immutable(tmp_path):
    repository, _ = _completed_second_review_with_one_conflict(tmp_path)
    conflict = repository.list_selection_conflicts(
        "selection-audit-v1", status="pending", offset=0, limit=100
    ).items[0]
    before = {
        sample.article.id: sample.label for sample in repository.list_gold_samples()
    }[conflict.article.id]

    saved = repository.save_selection_adjudication(
        "selection-audit-v1",
        conflict.article.id,
        _adjudication(selected=False, reason_code="too_niche"),
    )

    after = {
        sample.article.id: sample.label for sample in repository.list_gold_samples()
    }[conflict.article.id]
    assert saved.adjudication is not None
    assert after.selected is False
    assert after.relevant == before.relevant
    assert after.category == before.category
    assert after.annotator == before.annotator
    assert after.annotated_at == before.annotated_at
    assert after.annotation_method == before.annotation_method
    assert "[selection-audit-v1:adjudication:too_niche]" in after.notes
    with pytest.raises(AdjudicationAlreadyExistsError):
        repository.save_selection_adjudication(
            "selection-audit-v1",
            conflict.article.id,
            saved.adjudication,
        )


def test_consensus_item_rejects_adjudication(tmp_path):
    repository, items = _claimed_repository(tmp_path, count=1)
    repository.save_second_selection_review(
        "selection-audit-v1",
        items[0].candidate.article.id,
        _second_review(selected=True, reason_code="wechat_value"),
    )

    with pytest.raises(AdjudicationNotRequiredError):
        repository.save_selection_adjudication(
            "selection-audit-v1",
            items[0].candidate.article.id,
            _adjudication(selected=True, reason_code="wechat_value"),
        )


def test_reviewer_b_cannot_adjudicate(tmp_path):
    repository, _ = _completed_second_review_with_one_conflict(tmp_path)
    conflict = repository.list_selection_conflicts(
        "selection-audit-v1", status="pending", offset=0, limit=100
    ).items[0]

    with pytest.raises(AdjudicatorNotIndependentError):
        repository.save_selection_adjudication(
            "selection-audit-v1",
            conflict.article.id,
            _adjudication(
                adjudicator=" REVIEWER-B ",
                selected=False,
                reason_code="too_niche",
            ),
        )
