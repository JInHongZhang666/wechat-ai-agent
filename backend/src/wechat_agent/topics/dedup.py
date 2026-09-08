"""Deterministic canonicalization and de-duplication for source articles."""

from collections.abc import Sequence
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from wechat_agent.domain.models import ArticleRecord


_TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


def canonicalize_url(url: str) -> str:
    """Remove analytics-only URL parts while retaining content-identifying query data."""
    parsed = urlsplit(url.strip())
    kept_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMETERS
        and not key.lower().startswith("utm_")
    ]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            urlencode(sorted(kept_query)),
            "",
        )
    )


def _normalized_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return " ".join(filter(None, re.split(r"[^\w]+", normalized)))


def deduplicate(articles: Sequence[ArticleRecord]) -> list[ArticleRecord]:
    """Keep the first record for matching canonical URLs, then matching titles."""
    by_url: list[ArticleRecord] = []
    seen_urls: set[str] = set()
    for article in articles:
        canonical_url = canonicalize_url(article.source_url)
        if canonical_url not in seen_urls:
            seen_urls.add(canonical_url)
            by_url.append(article)

    deduplicated: list[ArticleRecord] = []
    seen_titles: set[str] = set()
    for article in by_url:
        normalized_title = _normalized_title(article.title)
        if normalized_title not in seen_titles:
            seen_titles.add(normalized_title)
            deduplicated.append(article)
    return deduplicated
