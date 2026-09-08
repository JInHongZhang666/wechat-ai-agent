from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from wechat_agent.evaluation.models import SelectionAdjudicationDecision
from wechat_agent.evaluation.review import (
    calculate_binary_agreement,
    normalize_reviewer_identity,
)


NOW = datetime(2026, 8, 4, tzinfo=UTC)


def test_reviewer_identity_uses_nfkc_trim_and_casefold():
    assert normalize_reviewer_identity("  Ｒｅｖｉｅｗｅｒ-Ｂ  ") == "reviewer-b"


def test_reviewer_identity_rejects_whitespace_only_values():
    with pytest.raises(ValueError, match="must not be blank"):
        normalize_reviewer_identity(" \t ")


def test_adjudication_reason_must_match_selected_direction():
    with pytest.raises(ValidationError):
        SelectionAdjudicationDecision(
            selected=False,
            reason_code="wechat_value",
            notes="",
            adjudicator="reviewer-a",
            adjudicated_at=NOW,
            annotation_method="human",
        )


def test_binary_agreement_reports_matrix_raw_agreement_and_kappa():
    result = calculate_binary_agreement(
        reviewer_a=[True, True, False, False],
        reviewer_b=[True, False, True, False],
    )
    assert result["matrix"] == {
        "a_keep_b_keep": 1,
        "a_keep_b_remove": 1,
        "a_remove_b_keep": 1,
        "a_remove_b_remove": 1,
    }
    assert result["rawAgreement"] == 0.5
    assert result["cohenKappa"] == 0.0
    assert result["undefinedReason"] is None


def test_binary_agreement_marks_zero_denominator_kappa_undefined():
    result = calculate_binary_agreement(
        reviewer_a=[True, True],
        reviewer_b=[True, True],
    )
    assert result["rawAgreement"] == 1.0
    assert result["cohenKappa"] is None
    assert result["undefinedReason"] == "expected_agreement_is_one"


def test_binary_agreement_rejects_unequal_review_sequences():
    with pytest.raises(ValueError, match="equal length"):
        calculate_binary_agreement(reviewer_a=[True], reviewer_b=[])
