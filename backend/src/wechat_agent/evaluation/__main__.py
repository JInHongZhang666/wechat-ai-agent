"""Command-line entry point for candidate export, validation and replay."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
from hashlib import sha256

from wechat_agent.domain.relevance import is_distribution_eligible
from wechat_agent.evaluation.io import read_gold
from wechat_agent.evaluation.models import (
    LabelingCandidate,
    PredictionSnapshot,
)
from wechat_agent.evaluation.release_gate import build_r1_evidence_report
from wechat_agent.evaluation.replay import replay_samples
from wechat_agent.evaluation.review import (
    SELECTION_REVIEW_SET_ID,
    build_double_review_report,
    build_selection_audit_report,
)
from wechat_agent.storage.sqlite import (
    SqliteArticleRepository,
    SqliteCurationDecisionRepository,
    SqliteEvaluationRepository,
    SqliteSourceRepository,
    SqliteSourceRunRepository,
)


def _parse_as_of(value: str) -> datetime:
    """Parse an explicit timezone-aware replay boundary."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--as-of must include a timezone offset")
    return parsed.astimezone(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-cabin-eval")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export-candidates",
        help="Export non-gold suggestions for manual annotation.",
    )
    export_parser.add_argument(
        "--database-url",
        default=os.getenv(
            "WECHAT_AGENT_DATABASE_URL",
            "sqlite:///data/wechat_agent.db",
        ),
    )
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument("--limit", type=int, default=200)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--input", type=Path, required=True)

    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("--input", type=Path, required=True)
    replay_parser.add_argument("--output", type=Path)

    export_gold_parser = subparsers.add_parser(
        "export-gold",
        help="Export completed human workbench labels as strict JSONL.",
    )
    export_gold_parser.add_argument(
        "--database-url",
        default=os.getenv(
            "WECHAT_AGENT_DATABASE_URL",
            "sqlite:///data/wechat_agent.db",
        ),
    )
    export_gold_parser.add_argument("--output", type=Path, required=True)
    export_gold_parser.add_argument("--force", action="store_true")

    double_export_parser = subparsers.add_parser(
        "export-double-reviewed-gold",
        help="Export human Gold only after double review and adjudication complete.",
    )
    double_export_parser.add_argument(
        "--database-url",
        default=os.getenv(
            "WECHAT_AGENT_DATABASE_URL",
            "sqlite:///data/wechat_agent.db",
        ),
    )
    double_export_parser.add_argument("--review-set-id", required=True)
    double_export_parser.add_argument("--output", type=Path, required=True)

    audit_parser = subparsers.add_parser(
        "selection-audit-report",
        help="Compare frozen first-pass gold with current reviewed labels.",
    )
    audit_parser.add_argument(
        "--database-url",
        default=os.getenv(
            "WECHAT_AGENT_DATABASE_URL",
            "sqlite:///data/wechat_agent.db",
        ),
    )
    audit_parser.add_argument("--baseline", type=Path, required=True)
    audit_parser.add_argument("--output", type=Path, required=True)

    double_report_parser = subparsers.add_parser(
        "selection-double-review-report",
        help="Write agreement, conflict, adjudication, and replay evidence.",
    )
    double_report_parser.add_argument(
        "--database-url",
        default=os.getenv(
            "WECHAT_AGENT_DATABASE_URL",
            "sqlite:///data/wechat_agent.db",
        ),
    )
    double_report_parser.add_argument("--review-set-id", required=True)
    double_report_parser.add_argument("--baseline", type=Path, required=True)
    double_report_parser.add_argument("--output", type=Path, required=True)

    gate_parser = subparsers.add_parser(
        "release-gate",
        help="Audit the R1 reliability and human-gold evidence gates.",
    )
    gate_parser.add_argument(
        "--database-url",
        default=os.getenv(
            "WECHAT_AGENT_DATABASE_URL",
            "sqlite:///data/wechat_agent.db",
        ),
    )
    gate_parser.add_argument(
        "--gold",
        type=Path,
        default=Path("evaluation/gold/news-gold-v1.jsonl"),
    )
    gate_parser.add_argument("--output", type=Path)
    gate_parser.add_argument("--days", type=int, default=7)
    gate_parser.add_argument("--required-gold", type=int, default=200)
    gate_parser.add_argument(
        "--as-of",
        type=_parse_as_of,
        help="Replay the gate at an ISO-8601 timestamp with timezone.",
    )
    gate_parser.add_argument(
        "--report-only",
        action="store_true",
        help="Return success after writing a blocked report.",
    )

    args = parser.parse_args(argv)
    if args.command == "export-candidates":
        count = export_candidates(args.database_url, args.output, args.limit)
        print(f"exported {count} labeling candidates to {args.output}")
        return 0

    if args.command == "release-gate":
        article_repository = SqliteArticleRepository(args.database_url)
        report = build_r1_evidence_report(
            SqliteSourceRepository(article_repository.engine),
            SqliteSourceRunRepository(article_repository.engine),
            args.gold,
            days=args.days,
            required_gold=args.required_gold,
            now=args.as_of,
        )
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
            print(f"wrote R1 evidence report to {args.output}")
        else:
            print(output)
        return 0 if report["passing"] or args.report_only else 2

    if args.command == "export-gold":
        article_repository = SqliteArticleRepository(args.database_url)
        repository = SqliteEvaluationRepository(article_repository.engine)
        samples = repository.list_gold_samples(human_only=True)
        try:
            write_versioned_text(
                args.output,
                "".join(
                    sample.model_dump_json() + "\n" for sample in samples
                ),
                force=args.force,
            )
        except FileExistsError as error:
            print(str(error), file=sys.stderr)
            return 2
        print(f"exported {len(samples)} human gold samples to {args.output}")
        return 0

    if args.command == "export-double-reviewed-gold":
        article_repository = SqliteArticleRepository(args.database_url)
        repository = SqliteEvaluationRepository(article_repository.engine)
        status = repository.double_review_status(args.review_set_id)
        if status.lifecycle != "complete":
            print(
                "refusing to export Gold before double review is complete",
                file=sys.stderr,
            )
            return 2
        samples = repository.list_gold_samples(human_only=True)
        try:
            write_versioned_text(
                args.output,
                "".join(sample.model_dump_json() + "\n" for sample in samples),
            )
        except FileExistsError as error:
            print(str(error), file=sys.stderr)
            return 2
        print(
            f"exported {len(samples)} double-reviewed human gold samples "
            f"to {args.output}"
        )
        return 0

    if args.command == "selection-audit-report":
        article_repository = SqliteArticleRepository(args.database_url)
        repository = SqliteEvaluationRepository(article_repository.engine)
        reviews = repository.list_review_items(
            SELECTION_REVIEW_SET_ID,
            status="all",
            category=None,
            offset=0,
            limit=10000,
        ).items
        report = build_selection_audit_report(
            review_set_id=SELECTION_REVIEW_SET_ID,
            reviews=reviews,
            before_samples=list(read_gold(args.baseline)),
            after_samples=repository.list_gold_samples(human_only=True),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote selection audit report to {args.output}")
        return 0

    if args.command == "selection-double-review-report":
        article_repository = SqliteArticleRepository(args.database_url)
        repository = SqliteEvaluationRepository(article_repository.engine)
        status = repository.double_review_status(args.review_set_id)
        if status.lifecycle != "complete":
            print(
                "refusing to report before double review is complete",
                file=sys.stderr,
            )
            return 2
        before_samples = list(read_gold(args.baseline))
        after_samples = repository.list_gold_samples(human_only=True)
        current_body = "".join(
            sample.model_dump_json() + "\n" for sample in after_samples
        )
        report = build_double_review_report(
            review_set_id=args.review_set_id,
            status=status,
            records=repository.list_double_review_records(args.review_set_id),
            before_samples=before_samples,
            after_samples=after_samples,
            baseline_sha256=sha256(args.baseline.read_bytes()).hexdigest(),
            current_sha256=sha256(current_body.encode("utf-8")).hexdigest(),
        )
        try:
            write_versioned_text(
                args.output,
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            )
        except FileExistsError as error:
            print(str(error), file=sys.stderr)
            return 2
        print(f"wrote selection double-review report to {args.output}")
        return 0

    samples = list(read_gold(args.input))
    if args.command == "validate":
        print(f"validated {len(samples)} fully labelled samples")
        return 0

    report = replay_samples(samples)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
        print(f"wrote replay report to {args.output}")
    else:
        print(output)
    return 0


def write_versioned_text(
    path: Path,
    body: str,
    *,
    force: bool = False,
) -> None:
    """Write a versioned artifact without silently replacing evidence."""
    if path.exists() and not force:
        raise FileExistsError(
            f"refusing to overwrite existing dataset: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8"))


def export_candidates(database_url: str, output: Path, limit: int) -> int:
    if limit < 1:
        raise ValueError("limit must be positive")
    article_repo = SqliteArticleRepository(database_url)
    decision_repo = SqliteCurationDecisionRepository(article_repo.engine)
    articles = article_repo.list_recent(datetime(1970, 1, 1, tzinfo=UTC), limit=limit)
    decisions = decision_repo.get_many([article.id for article in articles])
    candidates = []
    for article in articles:
        decision = decisions.get(article.id)
        candidates.append(
            LabelingCandidate(
                evaluation_group=(
                    f"{decision.strategy_version}:{decision.scored_at.isoformat()}"
                    if decision is not None
                    else f"unscored:{article.collected_at.isoformat()}"
                ),
                article=article,
                suggestion=PredictionSnapshot(
                    relevant=(decision.eligible if decision else is_distribution_eligible(article)),
                    category=article.category,
                    selected=(decision.selected if decision else False),
                    distribution_score=(decision.distribution_score if decision else None),
                    strategy_version=(decision.strategy_version if decision else None),
                    reason=(decision.reason if decision else None),
                ),
            )
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(
            candidate.model_dump_json() + "\n" for candidate in candidates
        ),
        encoding="utf-8",
    )
    return len(candidates)


if __name__ == "__main__":
    raise SystemExit(main())
