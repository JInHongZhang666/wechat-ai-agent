"""Quality checks that separate editable drafts from publishable drafts.

Phase 3.3 adds ``semantic_verify()`` — an LLM-powered check that replaces
the Phase 1 hard-block rule for edited content. When an LLM client is
available, free-form edits are validated against the evidence pack instead
of being unconditionally blocked.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from openai import OpenAI

from wechat_agent.domain.models import (
    DraftDocument,
    EvidencePack,
    QualityIssue,
    is_product_category,
)


_MARKDOWN = re.compile(r"[*#`_~]")
_NUMBER = re.compile(r"(?<![0-9A-Za-z_])\d+(?:[,.]\d+)*(?:[%％])?")
_ORDERED_LIST = re.compile(r"^\s*\d+[.)、]\s+", re.MULTILINE)
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((?:<)?[^)\s]+(?:>)?\)")
_URL = re.compile(r'(?:https?://|www\.)[^\s)<>"]+')
_PERSONAL_TEST = re.compile(
    r"亲测|实测|我试用|我用了|我测试|本人使用"
    r"|我(?:体验|上手|使用|试用|实测|测试)(?:了|过)?[^。！？\n]{0,20}(?:后|感受|认为|觉得|发现|结论)"
    r"|上手(?:了)?[一二三四五六七八九十两\d]+(?:天|周|个月)[^。！？\n]{0,20}(?:感受|后|认为|觉得|发现)"
)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def check_quality(
    draft: DraftDocument,
    pack: EvidencePack,
    *,
    trusted_body: str | None = None,
    trusted_titles: tuple[str, ...] | None = None,
    llm_client: OpenAI | None = None,
    llm_model: str = "deepseek-chat",
    profile=None,
    allow_confirmed_personal_experience: bool = False,
    allow_human_reviewed_edits: bool = False,
) -> list[QualityIssue]:
    """Return deterministic, machine-readable quality issues.

    Existing hard-block rules for paragraph length, source validation,
    numbers, personal-test claims, and weak titles are always applied.

    When ``llm_client`` is provided, free-form title and body edits are
    validated semantically instead of being unconditionally blocked.
    """
    issues: list[QualityIssue] = []
    supported_edges = {
        url for fact in pack.facts for url in fact.source_urls
    }
    cited_edges = set(draft.cited_source_urls)

    # --- Title edit verification ---
    if (
        trusted_titles is not None
        and draft.titles
        and draft.titles[0] not in trusted_titles
    ):
        if llm_client is not None:
            issues.extend(
                _semantic_title_check(
                    draft.titles[0], pack, llm_client, llm_model
                )
            )
        elif not allow_human_reviewed_edits:
            issues.append(
                QualityIssue(
                    code="edited_title_requires_verification",
                    message=(
                        "当前标题不属于最初生成的可信标题候选；Phase 1 无法自动"
                        "验证自由标题中的事实，需重新核实后才能审核或导出。"
                    ),
                )
            )
        else:
            issues.append(
                QualityIssue(
                    code="manual_title_review_required",
                    message="标题经过人工改写，发布前需要由作者确认其事实边界与表达准确性。",
                    blocking=False,
                    location="title",
                    fix_suggestion="结合证据包逐项核对标题中的主体、数字、日期和结论强度。",
                )
            )

    # --- Body edit verification ---
    if trusted_body is not None and draft.body != trusted_body:
        if llm_client is not None:
            issues.extend(
                _semantic_body_check(
                    draft.body, trusted_body, pack, llm_client, llm_model
                )
            )
        elif not allow_human_reviewed_edits:
            issues.append(
                QualityIssue(
                    code="edited_content_requires_verification",
                    message=(
                        "正文已偏离最初的可信生成版本；Phase 1 无法自动验证自由编辑"
                        "后的事实，需重新核实后才能审核或导出。"
                    ),
                )
            )
        else:
            issues.append(
                QualityIssue(
                    code="manual_content_review_required",
                    message="正文经过人工改写，发布前需要由作者确认新增表述均有依据。",
                    blocking=False,
                    location="body",
                    evidence_claim_ids=[fact.claim_id for fact in pack.facts],
                    fix_suggestion="重点核对新增事实、因果判断和绝对化结论；确认后再执行人工放行。",
                )
            )

    # --- Structural checks (always deterministic) ---
    paragraph_limit = profile.paragraph_limit if profile is not None else 80
    if _has_long_paragraph(draft.body, paragraph_limit):
        issues.append(
            QualityIssue(
                code="paragraph_too_long",
                message="正文包含超过八十个可见字符的段落，请拆分后再审核。",
                location="body",
                fix_suggestion="把超长段落拆成每段不超过账号画像限制的短段落。",
            )
        )

    if not cited_edges or not cited_edges <= supported_edges:
        issues.append(
            QualityIssue(
                code="missing_source",
                message="草稿缺少可追溯来源，或引用了事实支持边之外的链接。",
                location="citations",
                evidence_claim_ids=[fact.claim_id for fact in pack.facts],
                fix_suggestion="只保留证据包中的来源，并确保至少引用两个独立发布者。",
            )
        )

    if (
        is_product_category(pack.category)
        and _PERSONAL_TEST.search(draft.body)
        and not allow_confirmed_personal_experience
    ):
        issues.append(
            QualityIssue(
                code="unverified_personal_test",
                message="工具稿出现未经证据验证的个人体验表述。",
                location="body",
                fix_suggestion="删除亲测表述，或先在作者材料区录入并人工确认真实经验。",
            )
        )

    unsupported = _unsupported_numbers(draft.body, pack)
    if unsupported:
        issues.append(
            QualityIssue(
                code="unsupported_number",
                message=f"正文数字缺少事实支持：{', '.join(unsupported)}。",
                location="body",
                evidence_claim_ids=[fact.claim_id for fact in pack.facts if fact.numbers],
                fix_suggestion="删除这些数字，或补充包含对应数字的一手事实卡。",
            )
        )

    if _has_weak_titles(draft.titles):
        issues.append(
            QualityIssue(
                code="weak_title",
                message="需要五个互不重复、信息明确的标题候选。",
                location="title",
                fix_suggestion="保留五个信息明确且互不重复的标题方向。",
            )
        )

    if not pack.ready_for_draft or _independent_publishers(
        pack, cited_edges
    ) < 2:
        issues.append(
            QualityIssue(
                code="insufficient_sources",
                message="可信且独立的事实来源不足，当前草稿不可审核通过或导出。",
                location="evidence",
                fix_suggestion="至少补充两个独立发布者，其中一个应为已验证的一手来源。",
            )
        )

    if pack.conflicts:
        issues.append(
            QualityIssue(
                code="unresolved_evidence_conflict",
                message="证据包仍有未解决的重大事实冲突。",
                location="evidence",
                fix_suggestion="在证据区解决冲突，或在正文中明确使用不可确定表述。",
            )
        )

    if profile is not None:
        visible_length = len(re.sub(r"\s+", "", _MARKDOWN.sub("", draft.body)))
        minimum = max(300, int(profile.target_characters * 0.65))
        maximum = int(profile.target_characters * 1.35)
        if not minimum <= visible_length <= maximum:
            issues.append(
                QualityIssue(
                    code="profile_length_mismatch",
                    message=f"正文约 {visible_length} 字符，不符合账号画像 {minimum}-{maximum} 字符的硬约束。",
                    location="body",
                    fix_suggestion="按账号画像扩写或压缩正文，并保留现有事实与引用边界。",
                )
            )
        forbidden = [phrase for phrase in profile.forbidden_phrases if phrase and phrase in draft.body]
        if forbidden:
            issues.append(
                QualityIssue(
                    code="forbidden_phrase",
                    message=f"正文使用账号禁用表达：{', '.join(forbidden)}。",
                    location="body",
                    fix_suggestion="替换为准确、克制、不夸张的表达。",
                )
            )

    return issues


def semantic_verify(
    draft: DraftDocument,
    pack: EvidencePack,
    llm_client: OpenAI,
    *,
    model: str = "deepseek-chat",
    trusted_body: str | None = None,
    trusted_titles: tuple[str, ...] | None = None,
) -> list[QualityIssue]:
    """LLM-powered semantic edit verification — public convenience wrapper.

    Returns quality issues with refined confidence levels:
    - ``blocking=True`` for fact errors / fabricated claims
    - ``blocking=False`` for vague or unsupported but plausible claims (warnings)
    """
    return check_quality(
        draft, pack,
        trusted_body=trusted_body,
        trusted_titles=trusted_titles,
        llm_client=llm_client,
        llm_model=model,
    )


# ---------------------------------------------------------------------------
# Deterministic helpers (unchanged from Phase 1)
# ---------------------------------------------------------------------------


def _has_long_paragraph(body: str, limit: int = 80) -> bool:
    for paragraph in body.split("\n\n"):
        if not paragraph.strip() or paragraph.lstrip().startswith("## "):
            continue
        visible = _MARKDOWN.sub("", paragraph)
        visible = re.sub(r"\s+", "", visible)
        if len(visible) > limit:
            return True
    return False


def _unsupported_numbers(body: str, pack: EvidencePack) -> list[str]:
    supported = {
        _normalize_number(match.group())
        for fact in pack.facts
        for match in _NUMBER.finditer(fact.claim)
    }
    body_without_list_markers = _ORDERED_LIST.sub("", body)
    body_without_link_targets = _MARKDOWN_LINK.sub(
        lambda match: match.group(1), body_without_list_markers
    )
    body_without_urls = _URL.sub("", body_without_link_targets)
    topic_title = pack.topic_title.strip()
    body_without_topic_title = (
        body_without_urls.replace(topic_title, "")
        if topic_title
        else body_without_urls
    )
    found = {
        _normalize_number(match.group())
        for match in _NUMBER.finditer(body_without_topic_title)
    }
    return sorted(found - supported)


def _normalize_number(value: str) -> str:
    return value.replace(",", "").replace("％", "%")


def _has_weak_titles(titles: list[str]) -> bool:
    normalized = [
        re.sub(r"\s+", "", _MARKDOWN.sub("", title))
        for title in titles
    ]
    return (
        len(normalized) < 5
        or len(set(normalized)) < 5
        or any(len(title) < 8 for title in normalized)
    )


def _independent_publishers(
    pack: EvidencePack, cited_edges: set[str]
) -> int:
    identities: set[str] = set()
    for fact in pack.facts:
        for url in set(fact.source_urls) & cited_edges:
            source = fact.sources.get(url)
            if source is not None and source.source_identity.strip():
                identities.add(source.source_identity.strip().casefold())
            elif source is not None and source.source_name.strip():
                host = urlsplit(url).hostname or url
                identities.add(
                    f"{source.source_name.strip().casefold()}|{host.casefold()}"
                )
    return len(identities)


# ---------------------------------------------------------------------------
# LLM-powered semantic checks (Phase 3.3)
# ---------------------------------------------------------------------------

_SEMANTIC_BODY_PROMPT = """\
你是一个事实核查助手。你的任务是比较「编辑后的正文」与「原始生成正文」+「证据包」。

## 规则
- 只关注事实性差异：数据变化、新增/删除的断言、来源归属变化
- 不关注：措辞优化、结构调整、修辞手法
- 如果编辑引入的新事实在证据包中找不到支撑，报告为 issue
- 如果编辑修改了数字且与证据不一致，报告为阻断项
- 如果编辑删除了来源引用，报告为阻断项
- 如果编辑引入了"亲测""实测""我试用"等个人体验表述，报告为阻断项

## 输出格式
严格输出 JSON 数组，每个元素包含：
- code: 问题代码（字符串）
- message: 中文问题描述（字符串）
- blocking: 是否阻断（布尔值）

如果没有发现问题，输出空数组 []。

## 证据包
{evidence_summary}

## 原始生成正文
{trusted_body}

## 编辑后的正文
{edited_body}
"""


def _semantic_body_check(
    edited_body: str,
    trusted_body: str,
    pack: EvidencePack,
    client: OpenAI,
    model: str,
) -> list[QualityIssue]:
    """Ask the LLM to compare edited body vs. trusted body + evidence."""
    evidence_summary = _build_evidence_summary(pack)
    prompt = _SEMANTIC_BODY_PROMPT.format(
        evidence_summary=evidence_summary,
        trusted_body=trusted_body[:3000],
        edited_body=edited_body[:3000],
    )
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.0,
            max_tokens=1024,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if content is None:
            return _fallback_block("LLM semantic check returned empty response")
        data = json.loads(content)
        issues_raw = data if isinstance(data, list) else data.get("issues", [])
        return [
            QualityIssue(
                code=item.get("code", "semantic_issue"),
                message=item.get("message", "编辑内容可能存在事实偏差。"),
                blocking=item.get("blocking", True),
            )
            for item in issues_raw
            if isinstance(item, dict)
        ]
    except Exception:
        return _fallback_block("语义验证暂时不可用，编辑内容需人工核实。")


_SEMANTIC_TITLE_PROMPT = """\
你是一个标题事实核查助手。判断以下「自由编辑标题」是否引入证据包中无法支撑的新事实。

## 证据摘要
{evidence_summary}

## 自由编辑标题
{edited_title}

## 输出格式
严格输出 JSON：
{{"issues": [{{"code": "...", "message": "...", "blocking": true/false}}]}}
如果标题没有引入无法验证的事实，issues 为空数组。
"""


def _semantic_title_check(
    edited_title: str,
    pack: EvidencePack,
    client: OpenAI,
    model: str,
) -> list[QualityIssue]:
    """Ask the LLM whether a free-form title introduces unverifiable facts."""
    evidence_summary = _build_evidence_summary(pack)
    prompt = _SEMANTIC_TITLE_PROMPT.format(
        evidence_summary=evidence_summary,
        edited_title=edited_title,
    )
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.0,
            max_tokens=512,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if content is None:
            return _fallback_title_block()
        data = json.loads(content)
        issues_raw = data.get("issues", [])
        return [
            QualityIssue(
                code=item.get("code", "title_semantic_issue"),
                message=item.get("message", "标题可能包含无法验证的断言。"),
                blocking=item.get("blocking", True),
            )
            for item in issues_raw
            if isinstance(item, dict)
        ]
    except Exception:
        return _fallback_title_block()


def _build_evidence_summary(pack: EvidencePack) -> str:
    facts = "\n".join(
        f"- {fact.claim}（来源：{', '.join(fact.source_urls[:2])}）"
        for fact in pack.facts[:10]
    )
    unverified = "\n".join(
        f"- {claim}" for claim in (pack.unverified_claims or [])[:5]
    )
    return (
        f"主题：{pack.topic_title}\n"
        f"已验证事实：\n{facts}\n"
        f"待核实：\n{unverified}"
    )


def _fallback_block(message: str) -> list[QualityIssue]:
    return [
        QualityIssue(
            code="edited_content_requires_verification",
            message=message,
            blocking=True,
        )
    ]


def _fallback_title_block() -> list[QualityIssue]:
    return [
        QualityIssue(
            code="edited_title_requires_verification",
            message="语义验证暂时不可用，自由标题需人工核实。",
            blocking=True,
        )
    ]
