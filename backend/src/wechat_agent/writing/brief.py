"""Build and validate an evidence-bound content brief before drafting."""

from __future__ import annotations

from datetime import UTC, datetime

from wechat_agent.domain.models import (
    ContentBrief,
    ContentBriefSection,
    EvidencePack,
    is_product_category,
)


def build_content_brief(pack: EvidencePack) -> ContentBrief:
    verified = _verified_claims(pack)
    claim_ids = [fact.claim_id for fact in verified]
    if is_product_category(pack.category):
        structure = (
            ("what", "它是什么", "先用已核实事实说明产品、工具或能力发生了什么。"),
            ("value", "为什么值得关注", "解释对读者的实际价值，不把热度当结论。"),
            ("fit", "适合谁关注", "明确适用人群、使用场景与采用前提。"),
            ("limits", "采用前要确认什么", "列出证据边界、风险和需要作者补充的体验。"),
            ("judgement", "当前判断", "给出不超出证据的阶段性判断。"),
        )
        author_inputs = [
            "如需使用“亲测”或体验结论，请补充真实测试环境、步骤和结果。"
        ]
    else:
        structure = (
            ("event", "发生了什么", "按时间和来源说明已确认进展。"),
            ("impact", "为什么值得关注", "解释事件影响与读者收益。"),
            ("affected", "谁会受到影响", "区分直接影响、间接影响和暂不确定范围。"),
            ("judgement", "现在能下什么结论", "只使用已确认 claim 形成判断。"),
            ("next", "接下来观察什么", "列出尚未解决的变量，不把预测写成事实。"),
        )
        author_inputs = ["可补充作者自己的行业判断，但需与事实段落明确区分。"]

    sections = [
        ContentBriefSection(
            id=section_id,
            heading=heading,
            intent=intent,
            claim_ids=(claim_ids if index == 0 else claim_ids[:2]),
        )
        for index, (section_id, heading, intent) in enumerate(structure)
    ]
    blocking_reasons = brief_blocking_reasons(pack, sections)
    required_sources = sorted(
        {
            url
            for fact in verified
            for url in fact.source_urls
        }
    )
    risks = [
        *(
            [f"存在待核实信息：{claim}" for claim in pack.unverified_claims]
        ),
        *(
            [f"存在证据冲突：{conflict.subject}" for conflict in pack.conflicts]
        ),
    ]
    return ContentBrief(
        core_thesis=(
            f"围绕“{pack.topic_title}”，只基于可追溯证据解释发生了什么、"
            "对读者有什么价值，以及当前不能下哪些结论。"
        ),
        audience_benefit="帮助读者快速判断这条信息是否值得继续关注或采取行动。",
        angle=pack.suggested_angle or "先核实事实，再讨论价值与边界。",
        differentiation=(
            "借鉴灵感文章的选题与阅读节奏，但重建中心论点、论证顺序和表达；"
            "所有事实来自独立相关资料并保留引用，成稿必须通过项目原创阈值。"
            if pack.primary_inspiration_url
            else "不复述热搜，突出来源、证据强度、适用边界和待验证问题。"
        ),
        sections=sections,
        required_source_urls=required_sources,
        risks=risks,
        author_inputs=author_inputs,
        ready_for_draft=not blocking_reasons,
        blocking_reasons=blocking_reasons,
        updated_at=datetime.now(UTC),
    )


def validate_content_brief(
    brief: ContentBrief,
    pack: EvidencePack,
) -> ContentBrief:
    """Recalculate the non-bypassable evidence gate after user edits."""
    blocking_reasons = brief_blocking_reasons(pack, brief.sections)
    return brief.model_copy(
        update={
            "ready_for_draft": not blocking_reasons,
            "blocking_reasons": blocking_reasons,
            "updated_at": datetime.now(UTC),
        }
    )


def brief_blocking_reasons(
    pack: EvidencePack,
    sections: list[ContentBriefSection],
) -> list[str]:
    reasons: list[str] = []
    verified_ids = {fact.claim_id for fact in _verified_claims(pack)}
    referenced_ids = {
        claim_id
        for section in sections
        for claim_id in section.claim_ids
    }
    unknown = sorted(referenced_ids - {fact.claim_id for fact in pack.facts})
    if not pack.ready_for_draft:
        reasons.append("证据包尚未通过：存在未核实事实、冲突或缺少交叉验证。")
    if not verified_ids:
        reasons.append("没有可用于正式写作的已验证 claim。")
    if unknown:
        reasons.append(f"大纲引用了不存在的 claim：{', '.join(unknown)}。")
    if verified_ids and not (referenced_ids & verified_ids):
        reasons.append("文章结构尚未绑定任何已验证 claim。")
    if not sections or any(not section.heading.strip() for section in sections):
        reasons.append("文章结构至少需要一个具有明确标题的章节。")
    return list(dict.fromkeys(reasons))


def _verified_claims(pack: EvidencePack):
    unverified = {claim.strip().casefold() for claim in pack.unverified_claims}
    conflicting = {
        claim.strip().casefold()
        for conflict in pack.conflicts
        for claim in conflict.claims
    }
    return [
        fact
        for fact in pack.facts
        if fact.claim.strip().casefold() not in unverified | conflicting
    ]
