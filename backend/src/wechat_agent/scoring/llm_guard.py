"""Process-local safety guard for paid distribution-model calls.

Collection must remain available when the configured provider has no balance
or invalid credentials.  The guard also enforces a conservative daily request
budget so a noisy feed cannot silently consume an unlimited number of calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from threading import RLock

from wechat_agent.config import LlmConfig


@dataclass
class _GuardState:
    day: date
    calls: int = 0
    circuit_code: str | None = None
    opened_at: datetime | None = None


_LOCK = RLock()
_STATES: dict[str, _GuardState] = {}


def _key(config: LlmConfig) -> str:
    return config.base_url.rstrip("/").casefold()


def _state(config: LlmConfig) -> _GuardState:
    today = datetime.now(UTC).date()
    key = _key(config)
    state = _STATES.get(key)
    if state is None or state.day != today:
        state = _GuardState(day=today)
        _STATES[key] = state
    return state


def distribution_llm_available(config: LlmConfig | None) -> bool:
    if config is None or not config.is_available:
        return False
    with _LOCK:
        state = _state(config)
        return (
            state.circuit_code is None
            and state.calls < config.distribution_daily_call_limit
        )


def reserve_distribution_llm_call(config: LlmConfig) -> bool:
    """Reserve one provider call before sending any request."""
    with _LOCK:
        state = _state(config)
        if (
            state.circuit_code is not None
            or state.calls >= config.distribution_daily_call_limit
        ):
            return False
        state.calls += 1
        return True


def record_distribution_llm_failure(
    config: LlmConfig,
    error: Exception,
) -> str | None:
    status_code = getattr(error, "status_code", None)
    message = str(error).casefold()
    if status_code == 402 or "insufficient balance" in message:
        code = "LLM_INSUFFICIENT_BALANCE"
    elif status_code == 401 or "authentication" in message:
        code = "LLM_AUTH_FAILED"
    else:
        return None
    open_distribution_llm_circuit(config, code)
    return code


def open_distribution_llm_circuit(config: LlmConfig, code: str) -> None:
    with _LOCK:
        state = _state(config)
        state.circuit_code = code
        state.opened_at = datetime.now(UTC)


def reset_distribution_llm_circuit(config: LlmConfig) -> None:
    """Close only the provider-error circuit after a successful probe."""
    with _LOCK:
        state = _state(config)
        state.circuit_code = None
        state.opened_at = None


def distribution_llm_snapshot(config: LlmConfig | None) -> dict[str, object]:
    if config is None or not config.is_available:
        return {
            "enabled": False,
            "available": False,
            "state": "unconfigured",
            "callsToday": 0,
            "dailyCallLimit": 0,
            "reason": "LLM_NOT_CONFIGURED",
            "openedAt": None,
        }
    with _LOCK:
        state = _state(config)
        budget_exhausted = state.calls >= config.distribution_daily_call_limit
        reason = state.circuit_code or (
            "LLM_DAILY_BUDGET_EXHAUSTED" if budget_exhausted else None
        )
        return {
            "enabled": True,
            "available": reason is None,
            "state": "healthy" if reason is None else "degraded",
            "callsToday": state.calls,
            "dailyCallLimit": config.distribution_daily_call_limit,
            "reason": reason,
            "openedAt": state.opened_at.isoformat() if state.opened_at else None,
        }


def reset_distribution_llm_guard_for_tests() -> None:
    with _LOCK:
        _STATES.clear()
