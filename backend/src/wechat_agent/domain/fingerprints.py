"""Stable fingerprints for incremental article ingestion."""

from __future__ import annotations

import json
from hashlib import sha256

from wechat_agent.domain.models import ArticleRecord


def article_content_fingerprint(article: ArticleRecord) -> str:
    """Return the collector-input fingerprint retained across AI enrichment."""
    if article.ingestion_fingerprint:
        return article.ingestion_fingerprint
    content = json.dumps(
        {"title": article.title, "summary": article.summary},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(content.encode("utf-8")).hexdigest()
