"""Normalization helpers shared by public-source collectors."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
import re


_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def stable_id(prefix: str, value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def plain_text(value: str | None, *, limit: int = 600) -> str:
    text = _HTML_TAG.sub(" ", unescape(value or ""))
    normalized = _WHITESPACE.sub(" ", text).strip()
    return normalized[:limit]


def parse_datetime(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
