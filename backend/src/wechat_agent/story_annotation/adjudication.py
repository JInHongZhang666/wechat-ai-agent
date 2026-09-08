"""Append-only conflict adjudication validation."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .artifacts import canonical_json_bytes, canonical_jsonl_bytes, sha256_bytes
from .models import (
    AdjudicationRecord,
    AgreementReport,
    StoryAnnotationError,
    StoryAnnotationLabel,
    ValidatedAdjudication,
    ValidatedSubmission,
)


def label_sha256(label: StoryAnnotationLabel) -> str:
    return sha256_bytes(canonical_json_bytes(label))


def _labels_sha256(submission: ValidatedSubmission) -> str:
    return sha256_bytes(canonical_jsonl_bytes(submission.labels[key] for key in sorted(submission.labels)))


def validate_adjudications(
    report: AgreementReport,
    reviewer_a: ValidatedSubmission,
    reviewer_b: ValidatedSubmission,
    path: Path,
    *,
    agreement_sha256: str,
) -> ValidatedAdjudication:
    if (
        reviewer_a.sha256 != report.reviewer_a_sha256
        or reviewer_b.sha256 != report.reviewer_b_sha256
        or _labels_sha256(reviewer_a) != report.reviewer_a_labels_sha256
        or _labels_sha256(reviewer_b) != report.reviewer_b_labels_sha256
    ):
        raise StoryAnnotationError("ADJUDICATION_INPUT_DRIFT", "reviewer inputs differ from agreement inputs")
    try:
        body = Path(path).read_bytes()
    except OSError as error:
        raise StoryAnnotationError("ADJUDICATION_UNREADABLE", "adjudication file cannot be read") from error
    allowed = set(report.conflict_pair_ids)
    rows: dict[str, AdjudicationRecord] = {}
    for line_number, line in enumerate(body.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = AdjudicationRecord.model_validate(json.loads(line))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise StoryAnnotationError("INVALID_ADJUDICATION_ROW", "adjudication row is invalid", {"line": line_number}) from error
        if row.batch_id != report.batch_id or row.pair_id not in allowed:
            raise StoryAnnotationError("UNKNOWN_ADJUDICATION_PAIR", "only agreement conflicts may be adjudicated")
        if row.pair_id in rows:
            raise StoryAnnotationError("DUPLICATE_ADJUDICATION", "conflict appears more than once")
        if row.agreement_sha256 != agreement_sha256:
            raise StoryAnnotationError("ADJUDICATION_INPUT_DRIFT", "agreement hash does not match")
        if (
            row.reviewer_a_label_sha256 != label_sha256(reviewer_a.labels[row.pair_id])
            or row.reviewer_b_label_sha256 != label_sha256(reviewer_b.labels[row.pair_id])
        ):
            raise StoryAnnotationError("ADJUDICATION_INPUT_DRIFT", "reviewer label hash does not match")
        rows[row.pair_id] = row
    missing = tuple(sorted(allowed - set(rows)))
    return ValidatedAdjudication(
        sha256=sha256_bytes(body),
        completedCount=len(rows),
        missingPairIds=missing,
        rows=tuple(rows[key] for key in sorted(rows)),
    )
