"""Stage-specific evidence gates for the Story R2 rollout."""

from __future__ import annotations

from typing import Literal, Mapping

from .models import (
    AgreementReport,
    BatchManifest,
    GateBlocker,
    StoryAnnotationGateReport,
    ValidatedAdjudication,
)


Stage = Literal["pilot-50", "calibration-200", "gold-500"]


def build_release_gate(
    stage: Stage,
    manifest: BatchManifest,
    agreement: AgreementReport,
    adjudication: ValidatedAdjudication | None,
    *,
    algorithm_metrics: Mapping[str, float] | None = None,
    group_split_evidence: bool = False,
) -> StoryAnnotationGateReport:
    blockers: list[GateBlocker] = []

    def block(code: str, detail: str) -> None:
        blockers.append(GateBlocker(code=code, detail=detail))

    expected_count = {"pilot-50": 50, "calibration-200": 200, "gold-500": 500}[stage]
    if manifest.pair_count != expected_count or agreement.pair_count != expected_count:
        block("PAIR_COUNT_MISMATCH", f"{stage} requires exactly {expected_count} pairs")
    if agreement.batch_id != manifest.batch_id or agreement.candidate_sha256 != manifest.candidate_sha256:
        block("EVIDENCE_BINDING_MISMATCH", "agreement does not bind the supplied manifest")
    deterministic = agreement.deterministic_agreement
    if deterministic is None or deterministic < 0.85:
        block("AGREEMENT_BELOW_TARGET", "deterministic agreement must be at least 0.85")
    if agreement.hard_negative_agreement is not None and agreement.hard_negative_agreement < 0.80:
        block("HARD_NEGATIVE_AGREEMENT_BELOW_TARGET", "hard-negative agreement must be at least 0.80")
    if agreement.boundary_conflict_rate > 0.15:
        block("BOUNDARY_CONFLICT_RATE_ABOVE_TARGET", "boundary conflict rate must not exceed 0.15")
    adjudicated_pair_ids = set() if adjudication is None else {row.pair_id for row in adjudication.rows}
    if agreement.conflict_count and (
        adjudication is None
        or not adjudication.complete
        or adjudicated_pair_ids != set(agreement.conflict_pair_ids)
    ):
        block("ADJUDICATION_REQUIRED", "every reviewer conflict requires adjudication")
    has_positive = agreement.deterministic_matrix.get("yesYes", 0) > 0
    has_hard_negative = agreement.hard_negative_agreement is not None
    if not has_positive or not has_hard_negative:
        block("COVERAGE_INSUFFICIENT", "human evidence must contain positive and hard-negative pairs")

    if stage == "gold-500":
        if algorithm_metrics is None:
            block("ALGORITHM_METRICS_MISSING", "gold stage requires algorithm evaluation metrics")
        else:
            if algorithm_metrics.get("pairPrecision", -1) < 0.90:
                block("PAIR_PRECISION_BELOW_TARGET", "pair precision must be at least 0.90")
            if algorithm_metrics.get("pairRecall", -1) < 0.80:
                block("PAIR_RECALL_BELOW_TARGET", "pair recall must be at least 0.80")
            if algorithm_metrics.get("duplicateDisplayRate", 2) > 0.05:
                block("DUPLICATE_DISPLAY_RATE_ABOVE_TARGET", "duplicate display rate must not exceed 0.05")
        if not group_split_evidence:
            block("GROUP_SPLIT_EVIDENCE_MISSING", "gold stage requires Story-group split evidence")

    return StoryAnnotationGateReport(
        stage=stage,
        status="blocked" if blockers else "passing",
        blockers=tuple(blockers),
        publicCutoverEnabled=False,
    )
