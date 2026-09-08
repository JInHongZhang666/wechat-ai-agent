from datetime import UTC, datetime
import hashlib
import json

from wechat_agent.domain.models import ArticleRecord, TopicCategory
from wechat_agent.evaluation.__main__ import main
from wechat_agent.evaluation.models import (
    GoldLabel,
    GoldSample,
    LabelingCandidate,
    PredictionSnapshot,
    ReviewerClaimRequest,
    SelectionAdjudicationDecision,
    SelectionReviewDecision,
)
from wechat_agent.storage.sqlite import (
    SqliteArticleRepository,
    SqliteEvaluationRepository,
)


NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _decision(
    reviewer: str,
    *,
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


def _database_with_double_review(tmp_path, *, complete: bool):
    database_url = f"sqlite:///{tmp_path / ('complete.db' if complete else 'incomplete.db')}"
    article_repository = SqliteArticleRepository(database_url)
    repository = SqliteEvaluationRepository(article_repository.engine)
    candidates = []
    for position, category in enumerate(
        (TopicCategory.PAPER, TopicCategory.INDUSTRY), start=1
    ):
        article_id = f"double-review-cli-{position}"
        article = ArticleRecord(
            id=article_id,
            source_id=f"source:{article_id}",
            title=f"Double review CLI {position}",
            source_name="CLI Source",
            source_url=f"https://cli-source.ai/{article_id}",
            published_at=NOW,
            collected_at=NOW,
            summary="CLI fixture",
            category=category,
            source_identity=f"source:{article_id}",
            tier="t1",
        )
        candidates.append(
            LabelingCandidate(
                evaluation_group="double-review-cli",
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
                annotator="initial-reviewer",
                annotated_at=NOW,
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
    for item in items:
        repository.save_selection_review(
            "selection-audit-v1",
            item.candidate.article.id,
            _decision(
                "reviewer-a", selected=True, reason_code="wechat_value"
            ),
        )
    baseline_samples = repository.list_gold_samples(human_only=True)
    baseline = tmp_path / "news-gold-v1.1.jsonl"
    baseline.write_text(
        "".join(sample.model_dump_json() + "\n" for sample in baseline_samples),
        encoding="utf-8",
    )
    repository.claim_second_reviewer(
        "selection-audit-v1",
        ReviewerClaimRequest(reviewer="reviewer-b", claimed_at=NOW),
    )
    repository.save_second_selection_review(
        "selection-audit-v1",
        items[0].candidate.article.id,
        _decision("reviewer-b", selected=True, reason_code="wechat_value"),
    )
    if complete:
        conflict_id = items[1].candidate.article.id
        repository.save_second_selection_review(
            "selection-audit-v1",
            conflict_id,
            _decision("reviewer-b", selected=False, reason_code="too_niche"),
        )
        repository.save_selection_adjudication(
            "selection-audit-v1",
            conflict_id,
            SelectionAdjudicationDecision(
                selected=False,
                reason_code="too_niche",
                notes="Final human resolution.",
                adjudicator="reviewer-a",
                adjudicated_at=NOW,
                annotation_method="human",
            ),
        )
    return database_url, baseline


def test_double_review_gold_export_refuses_incomplete_lifecycle(tmp_path):
    database_url, _ = _database_with_double_review(tmp_path, complete=False)
    output = tmp_path / "news-gold-v1.2.jsonl"

    assert main([
        "export-double-reviewed-gold",
        "--database-url",
        database_url,
        "--review-set-id",
        "selection-audit-v1",
        "--output",
        str(output),
    ]) == 2
    assert not output.exists()


def test_double_review_gold_export_writes_complete_human_dataset_without_overwrite(
    tmp_path,
):
    database_url, _ = _database_with_double_review(tmp_path, complete=True)
    output = tmp_path / "news-gold-v1.2.jsonl"
    arguments = [
        "export-double-reviewed-gold",
        "--database-url",
        database_url,
        "--review-set-id",
        "selection-audit-v1",
        "--output",
        str(output),
    ]

    assert main(arguments) == 0
    rows = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 2
    assert all(row["label"]["annotation_method"] == "human" for row in rows)
    assert main(arguments) == 2


def test_double_review_report_contains_agreement_conflicts_reasons_and_metrics(
    tmp_path,
):
    database_url, baseline = _database_with_double_review(tmp_path, complete=True)
    gold_output = tmp_path / "news-gold-v1.2.jsonl"
    output = tmp_path / "selection-double-review-v1-report.json"

    assert main([
        "export-double-reviewed-gold",
        "--database-url",
        database_url,
        "--review-set-id",
        "selection-audit-v1",
        "--output",
        str(gold_output),
    ]) == 0

    assert main([
        "selection-double-review-report",
        "--database-url",
        database_url,
        "--review-set-id",
        "selection-audit-v1",
        "--baseline",
        str(baseline),
        "--output",
        str(output),
    ]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "complete"
    assert report["secondCompletedCount"] == report["totalCount"] == 2
    assert report["agreement"]["rawAgreement"] == 0.5
    assert report["agreement"]["cohenKappa"] == 0.0
    assert report["conflictCount"] == 1
    assert report["adjudicatedCount"] == 1
    assert report["reviewerAReasonCounts"] == {"wechat_value": 2}
    assert report["reviewerBReasonCounts"] == {
        "too_niche": 1,
        "wechat_value": 1,
    }
    assert report["finalReasonCounts"] == {
        "too_niche": 1,
        "wechat_value": 1,
    }
    assert report["beforeMetrics"]["sampleCount"] == 2
    assert report["afterMetrics"]["sampleCount"] == 2
    assert len(report["baselineSha256"]) == 64
    assert report["currentSha256"] == hashlib.sha256(
        gold_output.read_bytes()
    ).hexdigest()
