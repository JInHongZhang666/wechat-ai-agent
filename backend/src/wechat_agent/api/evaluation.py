"""Internal API for the human-operated R1 evaluation workbench."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Query
from fastapi.responses import Response

from wechat_agent.api.routes import ApiError
from wechat_agent.evaluation.models import (
    BlindSelectionReviewItem,
    BlindSelectionReviewPage,
    DoubleReviewStatus,
    GoldLabel,
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
    AdjudicationAlreadyExistsError,
    AdjudicationNotRequiredError,
    AdjudicatorNotIndependentError,
    ReviewAlreadyCompletedError,
    ReviewItemNotFoundError,
    ReviewSetNotFoundError,
    ReviewSubmissionAlreadyExistsError,
    ReviewerNotIndependentError,
    SecondReviewerAlreadyClaimedError,
    SecondReviewIncompleteError,
)
from wechat_agent.evaluation.workbench import EvaluationWorkbenchService


WorkbenchProvider = Callable[[], EvaluationWorkbenchService | None]


def create_evaluation_router(provider: WorkbenchProvider) -> APIRouter:
    """Expose internal operations without expanding the anonymous v1 contract."""
    router = APIRouter(prefix="/api/ops/evaluation")

    def service() -> EvaluationWorkbenchService:
        resolved = provider()
        if resolved is None:
            raise ApiError(
                503,
                "EVALUATION_STORAGE_UNAVAILABLE",
                "人工标注工作台当前只支持项目正式 SQLite 存储。",
                retryable=False,
            )
        return resolved

    @router.get("/status", response_model=WorkbenchStatus)
    def status() -> WorkbenchStatus:
        return service().status()

    @router.post("/candidates/refresh", response_model=WorkbenchStatus)
    def refresh_candidates() -> WorkbenchStatus:
        workbench = service()
        workbench.ensure_candidates()
        return workbench.status()

    @router.get("/candidates", response_model=WorkbenchPage)
    def list_candidates(
        status: str = Query("unlabelled", pattern="^(all|labelled|unlabelled)$"),
        category: str | None = Query(
            None,
            pattern="^(ai-models|ai-products|industry|paper|tip)$",
        ),
        q: str | None = Query(None, max_length=100),
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ) -> WorkbenchPage:
        return service().list_items(
            status=status,
            category=category,
            query=q,
            offset=offset,
            limit=limit,
        )

    @router.put(
        "/annotations/{article_id}",
        response_model=WorkbenchItem,
    )
    def save_annotation(article_id: str, label: GoldLabel) -> WorkbenchItem:
        try:
            return service().save_label(article_id, label)
        except KeyError as error:
            raise ApiError(
                404,
                "EVALUATION_CANDIDATE_NOT_FOUND",
                "待标注候选不存在。",
            ) from error
        except ValueError as error:
            raise ApiError(
                422,
                "INVALID_EVALUATION_LABEL",
                str(error),
            ) from error

    @router.post(
        "/reviews/selection-audit-v1/seed",
        response_model=SelectionReviewStatus,
    )
    def seed_selection_review() -> SelectionReviewStatus:
        return service().ensure_selection_review()

    @router.get(
        "/reviews/selection-audit-v1/status",
        response_model=SelectionReviewStatus,
    )
    def selection_review_status() -> SelectionReviewStatus:
        try:
            return service().selection_review_status()
        except ReviewSetNotFoundError as error:
            raise ApiError(
                404,
                "EVALUATION_REVIEW_SET_NOT_FOUND",
                "精选复核集合尚未初始化。",
            ) from error

    @router.get(
        "/reviews/selection-audit-v1/items",
        response_model=SelectionReviewPage,
    )
    def list_selection_reviews(
        status: str = Query("pending", pattern="^(all|pending|completed)$"),
        category: str | None = Query(
            None,
            pattern="^(ai-models|ai-products|industry|paper|tip)$",
        ),
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ) -> SelectionReviewPage:
        try:
            return service().list_selection_reviews(
                status=status,
                category=category,
                offset=offset,
                limit=limit,
            )
        except ReviewSetNotFoundError as error:
            raise ApiError(
                404,
                "EVALUATION_REVIEW_SET_NOT_FOUND",
                "精选复核集合尚未初始化。",
            ) from error

    @router.put(
        "/reviews/selection-audit-v1/items/{article_id}",
        response_model=SelectionReviewItem,
    )
    def save_selection_review(
        article_id: str,
        decision: SelectionReviewDecision,
    ) -> SelectionReviewItem:
        try:
            return service().save_selection_review(article_id, decision)
        except ReviewSetNotFoundError as error:
            raise ApiError(
                404,
                "EVALUATION_REVIEW_SET_NOT_FOUND",
                "精选复核集合尚未初始化。",
            ) from error
        except ReviewItemNotFoundError as error:
            raise ApiError(
                404,
                "EVALUATION_REVIEW_ITEM_NOT_FOUND",
                "该文章不在当前精选复核集合中。",
            ) from error
        except ReviewAlreadyCompletedError as error:
            raise ApiError(
                409,
                "EVALUATION_REVIEW_ALREADY_COMPLETED",
                "该条精选复核已经完成，不能覆盖审计记录。",
            ) from error
        except ValueError as error:
            raise ApiError(
                422,
                "INVALID_SELECTION_REVIEW",
                str(error),
            ) from error

    @router.get(
        "/reviews/selection-audit-v1/double-review/status",
        response_model=DoubleReviewStatus,
    )
    def double_review_status() -> DoubleReviewStatus:
        try:
            return service().double_review_status()
        except ReviewSetNotFoundError as error:
            raise _double_review_api_error(error) from error

    @router.post(
        "/reviews/selection-audit-v1/second-reviewer/claim",
        response_model=ReviewParticipant,
    )
    def claim_second_reviewer(
        claim: ReviewerClaimRequest,
    ) -> ReviewParticipant:
        try:
            return service().claim_second_reviewer(claim)
        except (
            ReviewSetNotFoundError,
            ReviewerNotIndependentError,
            SecondReviewerAlreadyClaimedError,
        ) as error:
            raise _double_review_api_error(error) from error

    @router.get(
        "/reviews/selection-audit-v1/second-review/items",
        response_model=BlindSelectionReviewPage,
    )
    def list_blind_selection_reviews(
        status: str = Query("pending", pattern="^(all|pending|completed)$"),
        category: str | None = Query(
            None,
            pattern="^(ai-models|ai-products|industry|paper|tip)$",
        ),
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ) -> BlindSelectionReviewPage:
        try:
            return service().list_blind_selection_reviews(
                status=status,
                category=category,
                offset=offset,
                limit=limit,
            )
        except (ReviewSetNotFoundError, ReviewerNotIndependentError) as error:
            raise _double_review_api_error(error) from error

    @router.put(
        "/reviews/selection-audit-v1/second-review/items/{article_id}",
        response_model=BlindSelectionReviewItem,
    )
    def save_second_selection_review(
        article_id: str,
        decision: SelectionReviewDecision,
    ) -> BlindSelectionReviewItem:
        try:
            return service().save_second_selection_review(article_id, decision)
        except (
            ReviewSetNotFoundError,
            ReviewItemNotFoundError,
            ReviewerNotIndependentError,
            ReviewSubmissionAlreadyExistsError,
        ) as error:
            raise _double_review_api_error(error) from error

    @router.get(
        "/reviews/selection-audit-v1/adjudication/items",
        response_model=SelectionConflictPage,
    )
    def list_selection_conflicts(
        status: str = Query("pending", pattern="^(all|pending|completed)$"),
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ) -> SelectionConflictPage:
        try:
            return service().list_selection_conflicts(
                status=status,
                offset=offset,
                limit=limit,
            )
        except (ReviewSetNotFoundError, SecondReviewIncompleteError) as error:
            raise _double_review_api_error(error) from error

    @router.put(
        "/reviews/selection-audit-v1/adjudication/items/{article_id}",
        response_model=SelectionConflictItem,
    )
    def save_selection_adjudication(
        article_id: str,
        decision: SelectionAdjudicationDecision,
    ) -> SelectionConflictItem:
        try:
            return service().save_selection_adjudication(article_id, decision)
        except (
            ReviewSetNotFoundError,
            ReviewItemNotFoundError,
            SecondReviewIncompleteError,
            AdjudicationNotRequiredError,
            AdjudicationAlreadyExistsError,
            AdjudicatorNotIndependentError,
        ) as error:
            raise _double_review_api_error(error) from error

    @router.get("/gold.jsonl")
    def download_gold() -> Response:
        body, _ = service().export_gold()
        return Response(
            content=body,
            media_type="application/x-ndjson; charset=utf-8",
            headers={
                "Content-Disposition": (
                    'attachment; filename="news-gold-current.jsonl"'
                )
            },
        )

    return router


def _double_review_api_error(error: Exception) -> ApiError:
    if isinstance(error, ReviewSetNotFoundError):
        return ApiError(404, "REVIEW_SET_NOT_FOUND", "精选复核集合尚未初始化。")
    if isinstance(error, ReviewItemNotFoundError):
        return ApiError(404, "REVIEW_ITEM_NOT_FOUND", "该文章不在复核集合中。")
    mappings: tuple[tuple[type[Exception], str, str], ...] = (
        (
            ReviewerNotIndependentError,
            "REVIEWER_NOT_INDEPENDENT",
            "第二复核人必须与初始标注人和第一复核人不同。",
        ),
        (
            SecondReviewerAlreadyClaimedError,
            "SECOND_REVIEWER_ALREADY_CLAIMED",
            "第二复核人已经绑定，不能更换。",
        ),
        (
            ReviewSubmissionAlreadyExistsError,
            "REVIEW_SUBMISSION_ALREADY_EXISTS",
            "该条第二人复核已经提交，不能覆盖。",
        ),
        (
            SecondReviewIncompleteError,
            "SECOND_REVIEW_INCOMPLETE",
            "第二人尚未完成全部复核，冲突区仍保持锁定。",
        ),
        (
            AdjudicationNotRequiredError,
            "ADJUDICATION_NOT_REQUIRED",
            "两位复核人意见一致，无需裁决。",
        ),
        (
            AdjudicationAlreadyExistsError,
            "ADJUDICATION_ALREADY_EXISTS",
            "该冲突已经裁决，不能覆盖。",
        ),
        (
            AdjudicatorNotIndependentError,
            "ADJUDICATOR_NOT_INDEPENDENT",
            "第二复核人不能裁决自己的复核结果。",
        ),
    )
    for error_type, code, message in mappings:
        if isinstance(error, error_type):
            return ApiError(409, code, message)
    return ApiError(422, "INVALID_DOUBLE_REVIEW", str(error))
