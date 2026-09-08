from datetime import UTC, datetime
import json

import pytest
from sqlalchemy import inspect

from wechat_agent.domain.models import ArticleRecord, TopicCategory
from wechat_agent.evaluation.models import (
    GoldLabel,
    GoldSample,
    LabelingCandidate,
    PredictionSnapshot,
    SelectionReviewDecision,
)
from wechat_agent.evaluation.__main__ import main
from wechat_agent.evaluation.review import ReviewAlreadyCompletedError
from wechat_agent.storage.sqlite import (
    SqliteArticleRepository,
    SqliteEvaluationRepository,
)


NOW = datetime(2026, 8, 3, tzinfo=UTC)


def _decision(
    *,
    selected: bool,
    reason_code: str,
) -> SelectionReviewDecision:
    return SelectionReviewDecision(
        selected=selected,
        reason_code=reason_code,
        reviewer="reviewer-b",
        reviewed_at=NOW,
        annotation_method="human",
    )


def _labelled_items(repository: SqliteEvaluationRepository, count: int = 2):
    candidates = []
    for position in range(1, count + 1):
        category = (
            TopicCategory.PAPER if position == 1 else TopicCategory.INDUSTRY
        )
        article_id = f"selection-review-{position}"
        article = ArticleRecord(
            id=article_id,
            source_id=f"source:{article_id}",
            title=f"Selection review {position}",
            source_name="Review Source",
            source_url=f"https://review-source.ai/{article_id}",
            published_at=NOW,
            collected_at=NOW,
            summary="Review fixture",
            category=category,
            source_identity=f"source:{article_id}",
            tier="t1" if position == 1 else "t2",
        )
        candidates.append(
            LabelingCandidate(
                evaluation_group="selection-review-fixture",
                article=article,
                suggestion=PredictionSnapshot(
                    relevant=True,
                    category=category,
                    selected=True,
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
                annotator="reviewer-a",
                annotated_at=NOW,
                notes="Initial label.",
            ),
        )
    return repository.list_items(status="labelled", limit=100).items


def test_sqlite_schema_preserves_review_tables_idempotently(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'review.db'}"
    repository = SqliteArticleRepository(database_url)
    SqliteArticleRepository(database_url)

    with repository.engine.connect() as connection:
        versions = connection.exec_driver_sql(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).scalars().all()
        tables = set(inspect(connection).get_table_names())

    assert versions[-1] == 18
    assert {
        "evaluation_review_sets",
        "evaluation_review_items",
    } <= tables


def test_review_set_seed_is_idempotent_and_freezes_initial_labels(tmp_path):
    article_repository = SqliteArticleRepository(
        f"sqlite:///{tmp_path / 'seed.db'}"
    )
    repository = SqliteEvaluationRepository(article_repository.engine)
    labelled_items = _labelled_items(repository)
    arguments = {
        "review_set_id": "selection-audit-v1",
        "kind": "selection",
        "target_count": 40,
        "criteria": {"seed": "selection-audit-v1"},
        "items": labelled_items,
    }

    assert repository.seed_review_set(**arguments) == len(labelled_items)
    assert repository.seed_review_set(**arguments) == 0

    page = repository.list_review_items(
        "selection-audit-v1",
        status="pending",
        category=None,
        offset=0,
        limit=100,
    )
    status = repository.review_status("selection-audit-v1")
    assert page.total == len(labelled_items)
    assert page.items[0].initial_label == labelled_items[0].label
    assert status.total_count == len(labelled_items)
    assert status.pending_count == len(labelled_items)


def test_selection_review_updates_current_gold_but_preserves_initial_label(
    tmp_path,
):
    article_repository = SqliteArticleRepository(
        f"sqlite:///{tmp_path / 'adjudicate.db'}"
    )
    repository = SqliteEvaluationRepository(article_repository.engine)
    labelled_items = _labelled_items(repository, count=1)
    repository.seed_review_set(
        review_set_id="selection-audit-v1",
        kind="selection",
        target_count=40,
        criteria={"seed": "selection-audit-v1"},
        items=labelled_items,
    )
    article_id = labelled_items[0].candidate.article.id

    reviewed = repository.save_selection_review(
        "selection-audit-v1",
        article_id,
        _decision(
            selected=False,
            reason_code="low_information_gain",
        ),
    )

    assert reviewed.initial_label.selected is True
    assert reviewed.review is not None
    assert reviewed.review.selected is False
    current = repository.list_gold_samples()[0].label
    assert current.selected is False
    assert current.annotator == "reviewer-b"
    assert "[selection-audit-v1:low_information_gain]" in current.notes
    with pytest.raises(ReviewAlreadyCompletedError):
        repository.save_selection_review(
            "selection-audit-v1",
            article_id,
            _decision(selected=True, reason_code="wechat_value"),
        )


def test_selection_audit_report_cli_writes_before_and_after_metrics(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'report.db'}"
    article_repository = SqliteArticleRepository(database_url)
    repository = SqliteEvaluationRepository(article_repository.engine)
    labelled_items = _labelled_items(repository, count=1)
    initial = labelled_items[0]
    repository.seed_review_set(
        review_set_id="selection-audit-v1",
        kind="selection",
        target_count=40,
        criteria={"seed": "selection-audit-v1"},
        items=labelled_items,
    )
    repository.save_selection_review(
        "selection-audit-v1",
        initial.candidate.article.id,
        _decision(selected=False, reason_code="low_information_gain"),
    )
    baseline = tmp_path / "news-gold-v1.jsonl"
    baseline.write_text(
        GoldSample(
            evaluation_group=initial.candidate.evaluation_group,
            article=initial.candidate.article,
            label=initial.label,
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "selection-audit-v1-report.json"

    assert main([
        "selection-audit-report",
        "--database-url",
        database_url,
        "--baseline",
        str(baseline),
        "--output",
        str(output),
    ]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "complete"
    assert report["changedCount"] == 1
    assert report["beforeMetrics"]["sampleCount"] == 1
    assert report["afterMetrics"]["sampleCount"] == 1
