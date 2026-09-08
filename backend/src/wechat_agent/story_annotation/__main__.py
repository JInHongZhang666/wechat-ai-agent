"""Command-line workflow for immutable Story annotation evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from pydantic import ValidationError

from .adjudication import validate_adjudications
from .agreement import build_agreement_report
from .artifacts import canonical_json_bytes, sha256_bytes, write_new_artifact
from .batches import initialize_batch
from .gate import build_release_gate
from .models import (
    AgreementReport,
    BatchManifest,
    StoryAnnotationError,
    ValidatedAdjudication,
)
from .submissions import validate_submission


def _timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StoryAnnotationError("INVALID_TIMESTAMP", "timestamp must include a timezone")
    return parsed


def _model(path: str, model_type):
    try:
        return model_type.model_validate_json(Path(path).read_bytes())
    except (OSError, ValidationError, ValueError) as error:
        raise StoryAnnotationError("INVALID_ARTIFACT", "artifact cannot be loaded", {"path": path}) from error


def _emit(value, *, stream=None) -> None:
    (stream or sys.stdout).write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m wechat_agent.story_annotation")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-batch")
    init.add_argument("--candidates", required=True)
    init.add_argument("--batch-dir", required=True)
    init.add_argument("--batch-id", required=True)
    init.add_argument("--strategy-version", required=True)
    init.add_argument("--created-at")
    validate = commands.add_parser("validate-submission")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--submission", required=True)
    validate.add_argument("--reviewer", required=True)
    agreement = commands.add_parser("agreement")
    agreement.add_argument("--manifest", required=True)
    agreement.add_argument("--reviewer-a", required=True)
    agreement.add_argument("--reviewer-b", required=True)
    agreement.add_argument("--output", required=True)
    agreement.add_argument("--generated-at")
    adjudication = commands.add_parser("validate-adjudication")
    adjudication.add_argument("--manifest", required=True)
    adjudication.add_argument("--agreement", required=True)
    adjudication.add_argument("--reviewer-a", required=True)
    adjudication.add_argument("--reviewer-b", required=True)
    adjudication.add_argument("--adjudication", required=True)
    adjudication.add_argument("--agreement-sha256", required=True)
    adjudication.add_argument("--output", required=True)
    gate = commands.add_parser("release-gate")
    gate.add_argument("--stage", choices=("pilot-50", "calibration-200", "gold-500"), required=True)
    gate.add_argument("--manifest", required=True)
    gate.add_argument("--agreement", required=True)
    gate.add_argument("--adjudication")
    gate.add_argument("--algorithm-metrics")
    gate.add_argument("--group-split-evidence", action="store_true")
    gate.add_argument("--output", required=True)
    return parser


def _run(args) -> tuple[int, dict]:
    if args.command == "init-batch":
        manifest = initialize_batch(
            Path(args.candidates), Path(args.batch_dir), batch_id=args.batch_id,
            strategy_version=args.strategy_version, created_at=_timestamp(args.created_at),
        )
        path = Path(args.batch_dir) / "manifest.json"
        return 0, {"status": "created", "artifact": str(path), "count": manifest.pair_count, "sha256": sha256_bytes(path.read_bytes())}
    if args.command == "validate-submission":
        manifest = _model(args.manifest, BatchManifest)
        result = validate_submission(manifest, Path(args.submission), args.reviewer)
        if not result.complete:
            raise StoryAnnotationError(
                "SUBMISSION_INCOMPLETE", "reviewer submission is incomplete",
                {"completedCount": result.completed_count, "requiredCount": manifest.pair_count},
            )
        return 0, {"status": "valid", "artifact": args.submission, "count": result.completed_count, "sha256": result.sha256}
    if args.command == "agreement":
        manifest = _model(args.manifest, BatchManifest)
        reviewer_b = validate_submission(manifest, Path(args.reviewer_b), "reviewer-b")
        if not reviewer_b.complete:
            raise StoryAnnotationError(
                "SECOND_REVIEW_INCOMPLETE", "Reviewer B must complete the batch before agreement is unlocked",
                {"completedCount": reviewer_b.completed_count, "requiredCount": manifest.pair_count},
            )
        reviewer_a = validate_submission(manifest, Path(args.reviewer_a), "reviewer-a")
        report = build_agreement_report(manifest, reviewer_a, reviewer_b, _timestamp(args.generated_at))
        body = canonical_json_bytes(report)
        write_new_artifact(Path(args.output), body)
        return 0, {"status": "created", "artifact": args.output, "count": report.pair_count, "sha256": sha256_bytes(body)}
    if args.command == "validate-adjudication":
        manifest = _model(args.manifest, BatchManifest)
        report = _model(args.agreement, AgreementReport)
        reviewer_b = validate_submission(manifest, Path(args.reviewer_b), "reviewer-b")
        if not reviewer_b.complete:
            raise StoryAnnotationError("SECOND_REVIEW_INCOMPLETE", "Reviewer B must complete the batch before adjudication")
        reviewer_a = validate_submission(manifest, Path(args.reviewer_a), "reviewer-a")
        result = validate_adjudications(
            report, reviewer_a, reviewer_b, Path(args.adjudication),
            agreement_sha256=args.agreement_sha256,
        )
        if not result.complete:
            raise StoryAnnotationError(
                "ADJUDICATION_INCOMPLETE", "not every conflict has been adjudicated",
                {"completedCount": result.completed_count, "requiredCount": report.conflict_count},
            )
        body = canonical_json_bytes(result)
        write_new_artifact(Path(args.output), body)
        return 0, {"status": "valid", "artifact": args.output, "count": result.completed_count, "sha256": sha256_bytes(body)}
    if args.command == "release-gate":
        manifest = _model(args.manifest, BatchManifest)
        report = _model(args.agreement, AgreementReport)
        adjudication = _model(args.adjudication, ValidatedAdjudication) if args.adjudication else None
        metrics = json.loads(Path(args.algorithm_metrics).read_text(encoding="utf-8")) if args.algorithm_metrics else None
        result = build_release_gate(
            args.stage, manifest, report, adjudication,
            algorithm_metrics=metrics, group_split_evidence=args.group_split_evidence,
        )
        body = canonical_json_bytes(result)
        write_new_artifact(Path(args.output), body)
        return (0 if result.status == "passing" else 2), {"status": result.status, "artifact": args.output, "count": manifest.pair_count, "sha256": sha256_bytes(body)}
    raise StoryAnnotationError("UNKNOWN_COMMAND", "unknown command")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        code, payload = _run(args)
        _emit(payload)
        return code
    except FileExistsError as error:
        _emit({"code": "ARTIFACT_EXISTS", "detail": "artifact already exists", "path": str(error)}, stream=sys.stderr)
        return 2
    except StoryAnnotationError as error:
        _emit({"code": error.code, "detail": error.detail, "context": error.context}, stream=sys.stderr)
        return 2
    except (ValidationError, json.JSONDecodeError, OSError, ValueError) as error:
        _emit({"code": "INVALID_INPUT", "detail": str(error)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
