"""Safe reference-page ingestion and copyright-safe structure analysis."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from html.parser import HTMLParser
import ipaddress
import re
import socket
import unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import uuid4

import httpx

from wechat_agent.domain.models import (
    ArticleRecord,
    EvidenceFact,
    EvidencePack,
    EvidenceSource,
    HeatConfidence,
    HeatScore,
    InspirationSource,
    InspirationStructure,
    SourceType,
    TopicCandidate,
    TopicCategory,
)


MAX_RESPONSE_BYTES = 2_000_000
MAX_CONTENT_CHARACTERS = 50_000
MAX_REDIRECTS = 5
_SPACE = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[。！？!?\.])\s*")
_NUMBER = re.compile(r"\d+(?:[,.]\d+)*(?:[%％])?")
_DATE = re.compile(r"(?:20\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?|\d{1,2}月\d{1,2}日)")
_VERSION = re.compile(r"\b(?:v|V)?\d+(?:\.\d+){1,3}(?:[-_][A-Za-z0-9.]+)?\b")
_TEXT_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote"}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}


class InspirationImportError(ValueError):
    """A user-visible URL validation, fetch or extraction failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_external_url(url: str, *, resolver=socket.getaddrinfo) -> str:
    """Return a canonical public HTTP(S) URL and reject SSRF targets."""
    value = url.strip()
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise InspirationImportError("INVALID_URL", "文章链接格式无效。") from exc
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise InspirationImportError("INVALID_URL_SCHEME", "只允许导入 HTTP 或 HTTPS 文章链接。")
    if not parsed.hostname or parsed.username or parsed.password:
        raise InspirationImportError("INVALID_URL", "文章链接必须包含公开域名，且不能携带账号信息。")
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise InspirationImportError("PRIVATE_URL_BLOCKED", "不能导入本机或内网地址。")
    try:
        addresses = {
            entry[4][0]
            for entry in resolver(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        }
    except (OSError, socket.gaierror) as exc:
        raise InspirationImportError("URL_DNS_FAILED", "文章域名暂时无法解析。") from exc
    if not addresses:
        raise InspirationImportError("URL_DNS_FAILED", "文章域名没有可用的公网地址。")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise InspirationImportError("PRIVATE_URL_BLOCKED", "不能导入指向本机、内网或保留网段的链接。")
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, ""))


def fetch_inspiration_source(
    url: str,
    *,
    role: str = "primary",
    source_kind: str = "user_url",
    known_article: ArticleRecord | None = None,
    client: httpx.Client | None = None,
) -> InspirationSource:
    """Fetch, extract and analyse one public article with redirect revalidation."""
    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=httpx.Timeout(15.0, connect=8.0),
        follow_redirects=False,
        trust_env=False,
        headers={
            "User-Agent": "AI-Information-Cabin/1.0 (+original-content-research)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
        },
    )
    current_url = validate_external_url(url)
    try:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with active_client.stream("GET", current_url) as streamed:
                    raw_parts: list[bytes] = []
                    raw_size = 0
                    for chunk in streamed.iter_bytes():
                        raw_size += len(chunk)
                        if raw_size > MAX_RESPONSE_BYTES:
                            raise InspirationImportError("ARTICLE_TOO_LARGE", "文章页面超过 2 MB，无法安全导入。")
                        raw_parts.append(chunk)
                    response = streamed
                    raw = b"".join(raw_parts)
            except httpx.HTTPError as exc:
                raise InspirationImportError("ARTICLE_FETCH_FAILED", "文章页面抓取失败，请检查链接后重试。") from exc
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise InspirationImportError("INVALID_REDIRECT", "文章页面返回了无目标的跳转。")
                current_url = validate_external_url(urljoin(current_url, location))
                continue
            if response.status_code >= 400:
                raise InspirationImportError(
                    "ARTICLE_FETCH_FAILED",
                    f"文章页面返回 HTTP {response.status_code}，暂时无法导入。",
                )
            content_type = response.headers.get("content-type", "").casefold()
            if content_type and not any(value in content_type for value in ("html", "text/plain", "xhtml")):
                raise InspirationImportError("UNSUPPORTED_CONTENT_TYPE", "该链接不是可解析的网页文章。")
            try:
                page_text = raw.decode(response.encoding or "utf-8", errors="replace")
            except LookupError:
                page_text = raw.decode("utf-8", errors="replace")
            title, content, structure, metadata = extract_article(page_text)
            if len(content) < 120:
                raise InspirationImportError(
                    "ARTICLE_CONTENT_TOO_SHORT",
                    "没有提取到足够的文章正文；页面可能需要登录或由脚本动态加载。",
                )
            final_url = str(response.url) if response.url else current_url
            return _to_source(
                url=final_url,
                title=title or (known_article.title if known_article else urlsplit(final_url).hostname or "未命名文章"),
                content=content,
                structure=structure,
                metadata=metadata,
                role=role,
                source_kind=source_kind,
                known_article=known_article,
            )
        raise InspirationImportError("TOO_MANY_REDIRECTS", "文章链接跳转次数过多。")
    finally:
        if owns_client:
            active_client.close()


def source_from_stored_article(article: ArticleRecord) -> InspirationSource:
    """Use a stored summary as supporting research, never as primary full text."""
    content = article.summary.strip()
    structure = analyse_structure([content], heading_count=0, list_count=0)
    return InspirationSource(
        id=str(uuid4()),
        role="supporting",
        source_kind="supporting_url",
        url=article.source_url,
        title=article.title,
        publisher=article.source_name,
        publisher_identity=article.source_identity or _publisher_identity(article.source_url),
        content_text=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        source_type=article.source_type,
        source_type_verified=article.source_type_verified,
        source_verification_basis=article.source_verification_basis,
        category=article.category,
        published_at=article.published_at,
        imported_at=datetime.now(UTC),
        structure=structure,
    )


def source_from_manual_text(
    url: str,
    content_text: str,
    *,
    title: str | None = None,
    role: str = "primary",
    source_kind: str = "user_url",
    known_article: ArticleRecord | None = None,
    resolver=socket.getaddrinfo,
) -> InspirationSource:
    """Build an inspiration source from user-supplied text after URL fetch fails.

    Manual primary text is intentionally treated only as inspiration material.
    It never becomes an evidence fact unless independent supporting sources
    confirm the claim through the normal EvidencePack workflow.
    """
    canonical_url = validate_external_url(url, resolver=resolver)
    raw_blocks = [
        value.strip()
        for value in re.split(r"\n\s*\n|\r?\n", content_text)
        if value.strip()
    ]
    blocks = _deduplicate_blocks(raw_blocks)
    content = "\n\n".join(blocks).strip()[:MAX_CONTENT_CHARACTERS]
    if len(re.sub(r"\s+", "", content)) < 120:
        raise InspirationImportError(
            "ARTICLE_CONTENT_TOO_SHORT",
            "补充正文过短，建议至少提供 120 个有效字符。",
        )
    heading_count = sum(bool(re.match(r"^#{1,6}\s+", value)) for value in raw_blocks)
    list_count = sum(bool(re.match(r"^(?:[-*+]\s+|\d+[.、]\s*)", value)) for value in raw_blocks)
    structure = analyse_structure(
        [re.sub(r"^(?:#{1,6}\s+|[-*+]\s+|\d+[.、]\s*)", "", value) for value in blocks],
        heading_count=heading_count,
        list_count=list_count,
    )
    fallback_title = (
        known_article.title if known_article is not None
        else (blocks[0][:80] if blocks else urlsplit(canonical_url).hostname or "手动补充文章")
    )
    return _to_source(
        url=canonical_url,
        title=(title or "").strip() or fallback_title,
        content=content,
        structure=structure,
        metadata={},
        role=role,
        source_kind=source_kind,
        known_article=known_article,
    )


def build_inspiration_research(
    *,
    topic_id: str,
    primary: InspirationSource,
    supporting: list[InspirationSource],
) -> tuple[TopicCandidate, list[ArticleRecord], EvidencePack]:
    """Build a source-attributed research pack without treating inspiration prose as facts."""
    now = datetime.now(UTC)
    distinct_supporting: list[InspirationSource] = []
    identities: set[str] = set()
    for source in supporting:
        if source.publisher_identity == primary.publisher_identity:
            continue
        if source.publisher_identity in identities:
            continue
        identities.add(source.publisher_identity)
        distinct_supporting.append(source)
    articles = [_source_to_article(primary), *[_source_to_article(source) for source in distinct_supporting]]
    candidate = TopicCandidate(
        id=topic_id,
        title=primary.title,
        category=primary.category,
        heat_score=HeatScore(value=0, confidence=HeatConfidence.ESTIMATED),
        summary=f"以《{primary.title}》为灵感原文，使用独立相关来源重新研究和原创表达。",
        recommendation_reason="用户指定或 AIHOT 当前高分文章；仅借鉴主题、结构和节奏，不复用原文表达。",
    )
    facts: list[EvidenceFact] = []
    for source in distinct_supporting:
        for sentence in _research_sentences(source.content_text)[:2]:
            claim = f"据{source.publisher}原文：{sentence}"
            fact_type = "date" if _DATE.search(claim) else "version" if _VERSION.search(claim) else "number" if _NUMBER.search(claim) else "fact"
            evidence_source = EvidenceSource(
                source_name=source.publisher,
                source_identity=source.publisher_identity,
                source_type=source.source_type,
                source_type_verified=source.source_type_verified,
                provenance=source.source_verification_basis,
            )
            facts.append(EvidenceFact(
                claim=claim,
                fact_type=fact_type,
                status="verified",
                subject=primary.title,
                numbers=_NUMBER.findall(claim),
                dates=_DATE.findall(claim),
                versions=_VERSION.findall(claim),
                last_verified_at=source.imported_at,
                source_name=source.publisher,
                source_url=source.url,
                source_urls=[source.url],
                sources={source.url: evidence_source},
            ))
    has_authoritative = any(
        source.source_type in {SourceType.OFFICIAL, SourceType.PRIMARY}
        and source.source_type_verified
        for source in distinct_supporting
    )
    ready = bool(facts) and len(distinct_supporting) >= 2 and has_authoritative
    blockers = []
    if len(distinct_supporting) < 2:
        blockers.append("至少需要两个与灵感原文不同发布者的相关来源。")
    if not has_authoritative:
        blockers.append("相关资料中缺少系统已验证的一手或官方来源。")
    if not facts:
        blockers.append("相关资料未提取到可引用的事实信息。")
    pack = EvidencePack(
        topic_id=topic_id,
        topic_title=primary.title,
        category=primary.category,
        facts=facts,
        source_urls=[source.url for source in distinct_supporting],
        unverified_claims=blockers,
        suggested_angle="从灵感原文的核心问题出发，补入独立来源，重新确定中心论点和信息顺序。",
        inspiration_blueprint=primary.structure.reusable_blueprint,
        primary_inspiration_url=primary.url,
        originality_threshold=0.10,
        ready_for_draft=ready,
    )
    return candidate, articles, pack


def related_stored_articles(
    primary: InspirationSource,
    candidates: list[ArticleRecord],
    *,
    limit: int = 5,
) -> list[ArticleRecord]:
    """Rank stored AIHOT items as independent supporting research."""
    primary_features = _features(f"{primary.title} {primary.content_text[:1500]}")
    ranked: list[tuple[float, ArticleRecord]] = []
    for article in candidates:
        if _same_url(article.source_url, primary.url):
            continue
        identity = article.source_identity or _publisher_identity(article.source_url)
        if identity == primary.publisher_identity:
            continue
        features = _features(f"{article.title} {article.summary}")
        overlap = len(primary_features & features) / max(1, len(features))
        title_overlap = len(_features(primary.title) & _features(article.title))
        category_bonus = 0.12 if article.category == primary.category else 0
        authority_bonus = 0.18 if article.source_type_verified and article.source_type in {SourceType.OFFICIAL, SourceType.PRIMARY} else 0
        score = overlap + min(0.25, title_overlap * 0.04) + category_bonus + authority_bonus
        if score >= 0.20:
            ranked.append((score, article))
    ranked.sort(key=lambda item: (item[0], item[1].published_at), reverse=True)
    selected: list[ArticleRecord] = []
    publishers: set[str] = set()
    for _, article in ranked:
        identity = article.source_identity or _publisher_identity(article.source_url)
        if identity in publishers:
            continue
        publishers.add(identity)
        selected.append(article)
        if len(selected) == limit:
            break
    return selected


def extract_article(html: str) -> tuple[str, str, InspirationStructure, dict[str, str]]:
    parser = _ArticleParser()
    parser.feed(html)
    preferred = _deduplicate_blocks(parser.preferred_blocks)
    blocks = preferred if len("".join(preferred)) >= 120 else _deduplicate_blocks(parser.blocks)
    content = "\n\n".join(blocks).strip()[:MAX_CONTENT_CHARACTERS]
    if len(content) < 120 and parser.description:
        content = f"{content}\n\n{parser.description}".strip()
        blocks = _deduplicate_blocks([*blocks, parser.description])
    title = _clean(parser.og_title or parser.title)
    structure = analyse_structure(
        blocks,
        heading_count=parser.heading_count,
        list_count=parser.list_count,
    )
    return title, content, structure, {
        "publisher": _clean(parser.publisher),
        "published_at": _clean(parser.published_at),
    }


def analyse_structure(
    blocks: list[str], *, heading_count: int, list_count: int
) -> InspirationStructure:
    paragraphs = [block for block in blocks if block]
    lengths = [len(re.sub(r"\s+", "", block)) for block in paragraphs]
    average = round(sum(lengths) / len(lengths)) if lengths else 0
    short_ratio = sum(length <= 80 for length in lengths) / len(lengths) if lengths else 0
    questions = sum(block.count("？") + block.count("?") for block in paragraphs)
    opening = "以问题切入" if paragraphs and ("？" in paragraphs[0] or "?" in paragraphs[0]) else "以事实或结论直接切题"
    blueprint = [opening]
    if heading_count:
        blueprint.append(f"采用约 {min(heading_count, 6)} 个主题段落推进")
    else:
        blueprint.append("按背景、事实、影响、判断重新组织")
    blueprint.append("短段落、高信息密度" if short_ratio >= 0.6 else "长短段结合")
    if list_count:
        blueprint.append("用清单呈现并列信息")
    blueprint.append("结尾给出边界与下一步观察")
    return InspirationStructure(
        heading_count=heading_count,
        paragraph_count=len(paragraphs),
        average_paragraph_characters=average,
        short_paragraph_ratio=round(short_ratio, 4),
        list_item_count=list_count,
        question_count=questions,
        opening_approach=opening,
        reusable_blueprint=blueprint,
    )


def _to_source(*, url: str, title: str, content: str, structure: InspirationStructure,
               metadata: dict[str, str], role: str, source_kind: str,
               known_article: ArticleRecord | None) -> InspirationSource:
    published_at = known_article.published_at if known_article else _parse_datetime(metadata.get("published_at", ""))
    publisher = (known_article.source_name if known_article else metadata.get("publisher")) or (urlsplit(url).hostname or "未知发布者")
    return InspirationSource(
        id=str(uuid4()), role=role, source_kind=source_kind, url=url,
        title=title, publisher=publisher,
        publisher_identity=(known_article.source_identity if known_article and known_article.source_identity else _publisher_identity(url)),
        content_text=content, content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        source_type=known_article.source_type if known_article else SourceType.SECONDARY,
        source_type_verified=known_article.source_type_verified if known_article else False,
        source_verification_basis=known_article.source_verification_basis if known_article else None,
        category=known_article.category if known_article else TopicCategory.INDUSTRY,
        published_at=published_at, imported_at=datetime.now(UTC), structure=structure,
    )


class _ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.preferred_blocks: list[str] = []
        self.title = ""
        self.og_title = ""
        self.publisher = ""
        self.published_at = ""
        self.description = ""
        self.heading_count = 0
        self.list_count = 0
        self._skip_depth = 0
        self._title_depth = 0
        self._active: list[tuple[str, list[str]]] = []
        self._scope_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {key.casefold(): (value or "") for key, value in attrs}
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._title_depth += 1
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").casefold()
            value = attributes.get("content", "")
            if key == "og:title": self.og_title = value
            elif key in {"og:site_name", "author"}: self.publisher = self.publisher or value
            elif key in {"article:published_time", "date", "pubdate"}: self.published_at = self.published_at or value
            elif key in {"description", "og:description"}: self.description = self.description or value
        identity = f"{attributes.get('id', '')} {attributes.get('class', '')}".casefold()
        if tag in {"article", "main"} or any(
            marker in identity
            for marker in ("rich_media_content", "js_content", "article-content", "post-content", "entry-content")
        ):
            self._scope_stack.append(tag)
        if tag in _TEXT_TAGS:
            self._active.append((tag, []))
            if tag.startswith("h"): self.heading_count += 1
            if tag == "li": self.list_count += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title": self._title_depth = max(0, self._title_depth - 1)
        for index in range(len(self._active) - 1, -1, -1):
            active_tag, parts = self._active[index]
            if active_tag != tag:
                continue
            self._active.pop(index)
            value = _clean("".join(parts))
            if len(value) >= 2:
                self.blocks.append(value)
                if self._scope_stack:
                    self.preferred_blocks.append(value)
            break
        if self._scope_stack and self._scope_stack[-1] == tag:
            self._scope_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._title_depth:
            self.title += data
        for _, parts in self._active:
            parts.append(data)


def _deduplicate_blocks(blocks: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in blocks:
        clean = _clean(value)
        key = re.sub(r"\W+", "", clean).casefold()
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _research_sentences(value: str) -> list[str]:
    sentences = []
    for sentence in _SENTENCE.split(value.replace("\n", " ")):
        clean = _clean(sentence)
        if 12 <= len(clean) <= 260 and not clean.casefold().startswith(("copyright", "免责声明")):
            sentences.append(clean)
    return sentences


def _source_to_article(source: InspirationSource) -> ArticleRecord:
    summary = " ".join(_research_sentences(source.content_text)[:3]) or source.content_text[:600]
    return ArticleRecord(
        id=f"inspiration:{source.id}", source_id=None, title=source.title,
        source_name=source.publisher, source_url=source.url,
        published_at=source.published_at or source.imported_at,
        collected_at=source.imported_at, summary=summary[:1200], category=source.category,
        source_type=source.source_type, source_identity=source.publisher_identity,
        source_type_verified=source.source_type_verified,
        source_verification_basis=source.source_verification_basis,
    )


def _features(value: str) -> set[str]:
    normalized = "".join(character.casefold() for character in unicodedata.normalize("NFKC", value) if character.isalnum() or character.isspace())
    words = {word for word in re.findall(r"[a-z0-9][a-z0-9._+-]{1,}|[\u4e00-\u9fff]{2,}", normalized) if len(word) >= 2}
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    words.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
    return words


def _same_url(left: str, right: str) -> bool:
    def canonical(value: str) -> tuple[str, str]:
        parsed = urlsplit(value)
        return ((parsed.hostname or "").casefold(), parsed.path.rstrip("/") or "/")
    return canonical(left) == canonical(right)


def _clean(value: str) -> str:
    return _SPACE.sub(" ", value or "").strip()


def _publisher_identity(url: str) -> str:
    return f"url-host:{(urlsplit(url).hostname or url).casefold()}"


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
