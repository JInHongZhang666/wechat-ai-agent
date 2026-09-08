"""Problem JSON error protocol (RFC 7807 compatible).

AIHOT-style standardized error responses with stable machine-readable
error codes, request IDs for diagnostics, and retryability hints.

Usage:
    raise AppError(
        status_code=404,
        code="NOT_FOUND",
        message="请求的资源不存在。",
    )
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ProblemDetail(BaseModel):
    """RFC 7807 Problem Details object."""
    type: str
    title: str
    status: int
    detail: str
    code: str
    requestId: str
    retryable: bool


class AppError(Exception):
    """Application-level error with stable code and HTTP status."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        problem_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.problem_type = problem_type or f"/problems/{code.lower()}"


def problem_response(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    problem_type: str | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    """Build a standard Problem JSON response."""
    detail = ProblemDetail(
        type=problem_type or f"/problems/{code.lower()}",
        title=_status_title(status_code),
        status=status_code,
        detail=message,
        code=code,
        requestId=request_id or f"req_{uuid4().hex[:12]}",
        retryable=retryable,
    )
    return JSONResponse(
        status_code=status_code,
        content=detail.model_dump(mode="json"),
        media_type="application/problem+json",
        headers={"X-Request-ID": detail.requestId},
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """FastAPI exception handler for AppError."""
    return problem_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        problem_type=exc.problem_type,
        request_id=getattr(request.state, "request_id", None),
    )


def _status_title(status_code: int) -> str:
    return {
        400: "Bad Request",
        404: "Not Found",
        409: "Conflict",
        422: "Unprocessable Entity",
        429: "Too Many Requests",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
    }.get(status_code, "Error")


# ── Common error factories ────────────────────────────────────────────

def not_found(message: str = "请求的资源不存在。") -> AppError:
    return AppError(404, "NOT_FOUND", message)


def invalid_request(message: str = "请求参数不合法。") -> AppError:
    return AppError(400, "INVALID_REQUEST", message)


def invalid_cursor(message: str = "游标已失效，请从第一页重新发起查询。") -> AppError:
    return AppError(400, "INVALID_CURSOR", message)


def snapshot_required(
    message: str = "同步游标已失效，请重新下载完整精选快照。",
) -> AppError:
    return AppError(409, "SNAPSHOT_REQUIRED", message)


def rate_limited(message: str = "请求过于频繁，请稍后重试。") -> AppError:
    return AppError(429, "RATE_LIMITED", message, retryable=True)


def temporarily_unavailable(message: str = "服务暂时不可用。") -> AppError:
    return AppError(503, "TEMPORARILY_UNAVAILABLE", message, retryable=True)


def internal_failure(message: str = "服务器处理失败。") -> AppError:
    return AppError(500, "INTERNAL_FAILURE", message, retryable=True)
