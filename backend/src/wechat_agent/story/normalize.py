"""Pure, deterministic normalization for Story candidate generation."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from wechat_agent.domain.models import ArticleRecord
from wechat_agent.story.models import NormalizedArticle


_TRACKING_KEYS = frozenset({"spm", "from", "ref"})
_SOURCE_SUFFIXES = (
    "量子位",
    "机器之心",
    "新智元",
    "36氪",
    "钛媒体",
    "techcrunch",
    "the verge",
)
_HYPE_PREFIX_RE = re.compile(r"^(?:重磅|快讯|独家|突发)(?:\s*[|｜:：\-—]\s*|\s+)")
_VERSION_RE = re.compile(r"(?<!\d)(?:v)?\d+(?:\.\d+){1,3}(?!\d)", re.IGNORECASE)
_ARXIV_RE = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)", re.IGNORECASE)
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")
_CHINESE_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_PUNCTUATION_RE = re.compile(r"[^\w\u3400-\u4dbf\u4e00-\u9fff.]+", re.UNICODE)

_ORGANIZATION_ALIASES: dict[str, tuple[str, ...]] = {
    "alibaba": ("阿里巴巴", "阿里", "alibaba"),
    "anthropic": ("anthropic",),
    "deepseek": ("deepseek", "深度求索"),
    "google": ("google", "谷歌"),
    "openai": ("openai",),
}
_PRODUCT_ALIASES: dict[str, tuple[str, ...]] = {
    "claude": ("claude",),
    "deepseek": ("deepseek",),
    "gemini": ("gemini",),
    "gpt": ("gpt", "chatgpt"),
    "qwen": ("qwen", "通义千问", "千问"),
}
_PEOPLE_ALIASES: dict[str, tuple[str, ...]] = {}
_ACTION_ALIASES: dict[str, tuple[str, ...]] = {
    "acquisition": ("收购", "acquire", "acquisition"),
    "funding": ("融资", "funding", "raised"),
    "lawsuit": ("诉讼", "起诉", "lawsuit", "sued"),
    "open_source": ("开源", "open source", "open-source"),
    "release": ("发布", "上线", "推出", "release", "launch"),
    "regulation": ("监管", "处罚", "regulation", "fine"),
    "shutdown": ("下架", "停止服务", "shutdown", "discontinue"),
    "update": ("更新", "升级", "update", "upgrade"),
}


def _normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Story normalization requires an http(s) source URL")

    host = parts.hostname.casefold()
    port = parts.port
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_KEYS
    )
    return urlunsplit((scheme, host, path, urlencode(query, doseq=True), ""))


def _normalize_title(title: str, source_name: str) -> str:
    value = unicodedata.normalize("NFKC", title).casefold().strip()
    value = _HYPE_PREFIX_RE.sub("", value)

    suffixes = tuple(
        suffix.casefold()
        for suffix in (*_SOURCE_SUFFIXES, source_name.strip())
        if suffix.strip()
    )
    if suffixes:
        escaped = "|".join(re.escape(suffix) for suffix in sorted(suffixes, key=len, reverse=True))
        value = re.sub(rf"\s*[-–—|｜]\s*(?:{escaped})\s*$", "", value)

    value = _PUNCTUATION_RE.sub(" ", value)
    return " ".join(value.split())


def _title_shingles(normalized_title: str) -> frozenset[str]:
    shingles: set[str] = set()
    for run in _CHINESE_RUN_RE.findall(normalized_title):
        for size in (2, 3):
            shingles.update(run[index : index + size] for index in range(len(run) - size + 1))
    for token in _LATIN_TOKEN_RE.findall(normalized_title):
        shingles.add(token)
        compact = token.replace(".", "")
        if len(compact) >= 3:
            shingles.update(compact[index : index + 3] for index in range(len(compact) - 2))
    return frozenset(shingles)


def _extract_aliases(text: str, aliases: dict[str, tuple[str, ...]]) -> frozenset[str]:
    return frozenset(
        canonical
        for canonical, candidates in aliases.items()
        if any(candidate in text for candidate in candidates)
    )


def _extract_identifiers(text: str, source_url: str) -> frozenset[str]:
    identifiers = {f"arxiv:{match}" for match in _ARXIV_RE.findall(text)}
    parts = urlsplit(source_url)
    if parts.hostname and parts.hostname.casefold() in {"github.com", "www.github.com"}:
        segments = [segment.casefold() for segment in parts.path.split("/") if segment]
        if len(segments) >= 2:
            identifiers.add(f"github:{segments[0]}/{segments[1].removesuffix('.git')}")
    return frozenset(identifiers)


def normalize_article(article: ArticleRecord) -> NormalizedArticle:
    """Derive matching features without altering the persisted article."""
    normalized_title = _normalize_title(article.title, article.source_name)
    searchable = unicodedata.normalize(
        "NFKC", f"{article.title}\n{article.summary}"
    ).casefold()
    versions = frozenset(
        match.casefold().removeprefix("v") for match in _VERSION_RE.findall(searchable)
    )
    return NormalizedArticle(
        article=article,
        normalized_url=_normalize_url(article.source_url),
        normalized_title=normalized_title,
        title_shingles=_title_shingles(normalized_title),
        organizations=_extract_aliases(searchable, _ORGANIZATION_ALIASES),
        products=_extract_aliases(searchable, _PRODUCT_ALIASES),
        people=_extract_aliases(searchable, _PEOPLE_ALIASES),
        versions=versions,
        identifiers=_extract_identifiers(searchable, article.source_url),
        action_codes=_extract_aliases(searchable, _ACTION_ALIASES),
    )
