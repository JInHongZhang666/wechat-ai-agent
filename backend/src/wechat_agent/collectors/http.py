"""Small shared HTTP boundary for collectors and deterministic tests."""

from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Any, Protocol

import httpx


class HttpResponse(Protocol):
    text: str
    status_code: int

    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


class HttpGetter(Protocol):
    def get(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse: ...


class RetryingHttpGetter:
    """Bounded retry boundary for transient transport and upstream failures."""

    def __init__(
        self,
        client: HttpGetter,
        *,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.2,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._client = client
        self._max_attempts = max_attempts
        self._base_delay_seconds = max(0.0, base_delay_seconds)
        self._sleep = sleep_fn

    def get(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        last_error: httpx.RequestError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.RequestError as error:
                last_error = error
                if attempt == self._max_attempts:
                    raise
                self._backoff(attempt)
                continue
            if not _is_retryable_status(response.status_code):
                return response
            if attempt == self._max_attempts:
                return response
            self._backoff(attempt)
        if last_error is not None:  # pragma: no cover - loop always returns/raises
            raise last_error
        raise RuntimeError("retry loop completed without a response")

    def _backoff(self, attempt: int) -> None:
        self._sleep(self._base_delay_seconds * (2 ** (attempt - 1)))


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code <= 599


def build_http_client(timeout_seconds: float = 12.0) -> HttpGetter:
    client = httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=True,
        headers={
            "User-Agent": "wechat-ai-agent/0.2 (+local research radar)",
        },
    )
    return RetryingHttpGetter(client)
