"""Deterministic and LLM-powered draft providers.

``RuleBasedDraftProvider`` is the zero-dependency fallback. It always
produces a structurally valid research draft from evidence, without
inventing facts or requiring an API key.

``LlmDraftProvider`` uses the OpenAI-compatible chat API (DeepSeek by
default) to generate original articles while respecting the same
evidence-bound constraints as the rule-based provider.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from uuid import uuid4

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    OpenAI,
    RateLimitError,
)

from wechat_agent.config import LlmConfig
from wechat_agent.domain.models import (
    DraftDocument,
    ContentBrief,
    EvidenceFact,
    EvidencePack,
    GenerationOutcome,
    LlmCallRecord,
    PromptVersion,
    TopicCategory,
    WritingProfile,
    is_product_category,
)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
你是"AI信息小屋"的专职撰稿人，面向希望高效了解实用 AI 的公众号读者。

## 核心原则
1. 只能使用证据包中提供的事实，绝不虚构任何数据、体验、采访、用户评价或出处。
2. 从多个来源交叉验证后的事实才能作为确定结论；单一来源的说法需标注"待核实"。
3. 工具类文章，没有真实试用记录时，只生成"研究初稿"，不写"亲测""实测"。
4. 语言简洁具体，对非技术读者友好，避免空泛开场，直接进入主题。
5. 每段原则上不超过 80 个汉字。
6. 若提供灵感文章蓝图，只能借鉴主题角度、信息组织和阅读节奏；禁止复用原文句子、段落顺序或冒充原作者文风。
7. 事实可以引用并必须注明来源，但所有分析、过渡和结论都要重新论证、重新表达，不能做同义词替换式改写。

## 工具类文章结构
- 工具是什么（已核实信息）
- 为什么会受到关注
- 适合谁关注
- 采用前要看什么
- 当前判断

## 热点时讯类文章结构
- 发生了什么（已核实信息）
- 为什么值得关注
- 谁会受到影响
- 现在能下什么结论
- 接下来观察什么

## 输出格式
严格输出 JSON，不包含任何其他文字：
```json
{
  "titles": ["标题1", "标题2", "标题3", "标题4", "标题5"],
  "body": "正文（Markdown 格式，## 小标题，**加粗**重点）",
  "cited_source_urls": ["url1", "url2"]
}
```

- titles: 5 个互不重复的原创标题，从不同角度切入，每个 10-25 个汉字
- body: 约 800 字正文，使用 ## 小标题分段，**加粗**关键结论，段落简短
- cited_source_urls: 正文中实际引用了哪些来源 URL（必须是 evidence 中出现的）
"""

_PROMPT_VERSION = 2
_OUTLINE_PROMPT = """根据已审核 Content Brief 规划全新的标题和章节。只能引用允许的 claim_id。灵感蓝图只表示抽象阅读节奏，必须重新确定论证顺序，禁止复刻原文目录。输出 JSON：{\"titles\":[5个标题],\"sections\":[{\"id\":\"...\",\"heading\":\"...\",\"intent\":\"...\",\"claim_ids\":[\"...\"]}]}。"""
_SECTION_PROMPT = """逐节撰写公众号原创研究稿。每节只能使用该节允许的 claim_id 对应事实；事实要标明来源并重新表述，禁止复制灵感原文句子或只替换同义词；没有作者确认材料时禁止第一人称体验。输出 JSON：{\"sections\":[{\"id\":\"...\",\"body\":\"Markdown正文\"}],\"cited_source_urls\":[\"...\"]}。"""
_EDITOR_PROMPT = """将逐节研究稿整合为完整原创文章。不得新增事实、数字、日期、版本、引语或体验；不得复刻灵感原文的句子和段落顺序；保持短段落、克制标题和明确行动建议。输出 JSON：{\"titles\":[5个标题],\"body\":\"Markdown正文\",\"cited_source_urls\":[\"...\"]}。"""
_POLISH_PROMPT = """对当前研究稿执行发表前深度润色。删除模板化开场、机械排比、空泛总结和生硬连接词，改善口语自然度、段落节奏与观点密度；保留全部有依据的事实和来源，不得新增事实、数字、日期、引语或个人体验。必须重新检查与灵感原文的表达距离，不能通过同义词替换伪装改写。输出 JSON：{\"titles\":[5个标题],\"body\":\"Markdown正文\",\"cited_source_urls\":[\"...\"]}。"""
_PROBE_PROMPT = """你正在执行模型连通性检查，不生成文章。严格输出 JSON：{\"ok\":true}，不要输出其他内容。"""
_FALLBACK_PROMPT = "确定性规则生成器：只拼装已验证 claim 和已审核 Brief。"


def _template_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def built_in_prompt_versions() -> list[PromptVersion]:
    """Return immutable prompt contracts without exposing runtime secrets."""
    definitions = (
        ("probe", _PROBE_PROMPT, "creation-probe-v1"),
        ("outline", _OUTLINE_PROMPT, "creation-outline-v1"),
        ("section", _SECTION_PROMPT, "creation-sections-v1"),
        ("editor", _EDITOR_PROMPT, "creation-editor-v1"),
        ("fallback", _FALLBACK_PROMPT, "draft-document-v1"),
    )
    return [
        PromptVersion(
            id=f"creation-{stage}-v{_PROMPT_VERSION}",
            stage=stage,
            version=_PROMPT_VERSION,
            template_hash=_template_hash(template),
            output_schema=schema,
        )
        for stage, template, schema in definitions
    ]


_PROMPTS = {item.stage: item for item in built_in_prompt_versions()}


def classify_llm_error(exc: Exception) -> str:
    """Map provider-specific exceptions to stable, user-safe error codes."""
    status_code = getattr(exc, "status_code", None)
    if status_code == 402 or "insufficient balance" in str(exc).casefold():
        return "LLM_INSUFFICIENT_BALANCE"
    if isinstance(exc, AuthenticationError):
        return "LLM_AUTH_FAILED"
    if isinstance(exc, RateLimitError):
        return "LLM_RATE_LIMITED"
    if isinstance(exc, (APITimeoutError, TimeoutError)):
        return "LLM_TIMEOUT"
    if isinstance(exc, (APIConnectionError, ConnectionError)):
        return "LLM_CONNECTION_FAILED"
    if isinstance(exc, NotFoundError):
        return "LLM_MODEL_NOT_FOUND"
    if isinstance(exc, BadRequestError):
        return "LLM_BAD_REQUEST"
    if isinstance(exc, InternalServerError):
        return "LLM_PROVIDER_ERROR"
    if isinstance(exc, json.JSONDecodeError):
        return "LLM_INVALID_JSON"
    if isinstance(exc, ValueError) and "empty response" in str(exc).casefold():
        return "LLM_EMPTY_RESPONSE"
    return "LLM_UNKNOWN_ERROR"


def llm_error_message(code: str | None) -> str:
    """Return a concise operator-facing message without leaking credentials."""
    return {
        "LLM_INSUFFICIENT_BALANCE": "模型账户余额不足，请充值后重新验证。",
        "LLM_AUTH_FAILED": "模型鉴权失败，请更换有效密钥后重启服务。",
        "LLM_RATE_LIMITED": "模型服务触发限流，请稍后重试或检查账户额度。",
        "LLM_TIMEOUT": "模型响应超时，请检查网络或提高超时时间。",
        "LLM_CONNECTION_FAILED": "无法连接模型服务，请检查 API 地址和网络。",
        "LLM_MODEL_NOT_FOUND": "模型名称不可用，请检查当前模型配置。",
        "LLM_BAD_REQUEST": "模型拒绝了请求，请检查模型能力和 JSON 输出配置。",
        "LLM_PROVIDER_ERROR": "模型服务端暂时异常，请稍后重试。",
        "LLM_INVALID_JSON": "模型返回内容不是有效 JSON。",
        "LLM_EMPTY_RESPONSE": "模型返回了空内容。",
        "LLM_UNKNOWN_ERROR": "模型调用失败，请查看内部调用记录。",
    }.get(code, "模型尚未完成真实连通性验证。")


def _is_retryable_llm_error(exc: Exception) -> bool:
    return isinstance(
        exc,
        (RateLimitError, APITimeoutError, TimeoutError, APIConnectionError, ConnectionError, InternalServerError),
    )


def _safe_error_detail(exc: Exception) -> str:
    value = " ".join(str(exc).split())
    return value[:240] if value else type(exc).__name__


def _build_user_prompt(
    pack: EvidencePack,
    profile: WritingProfile,
    brief: ContentBrief | None = None,
) -> str:
    """Build a structured user prompt from the evidence pack and profile."""
    category_label = "工具/项目" if is_product_category(pack.category) else "热点时讯"
    verified = _verified_facts(pack)
    unverified = pack.unverified_claims or []
    conflicts = [
        f"- {conflict.subject}: {' vs '.join(conflict.claims[:3])}"
        for conflict in pack.conflicts
    ]

    facts_text = "\n".join(
        f"- [{'已验证' if f.claim.strip().casefold() not in {c.strip().casefold() for c in unverified} else '待核实'}] {f.claim}"
        f"（来源：{', '.join(f.source_urls[:2])}）"
        for f in pack.facts
    ) if pack.facts else "（当前无事实卡）"

    sources_text = "\n".join(
        f"- {url}" for url in pack.source_urls
    ) if pack.source_urls else "（无来源）"

    brief_text = (
        "\n".join(
            f"- {section.heading}：{section.intent}"
            f"（允许使用 claim：{', '.join(section.claim_ids) or '无'}）"
            for section in brief.sections
        )
        if brief is not None
        else "（尚未提供 Content Brief）"
    )

    return f"""## 选题信息
- 主题：{pack.topic_title}
- 类型：{category_label}
- 建议角度：{pack.suggested_angle or '无'}

## 证据事实
{facts_text}

## 冲突信息
{chr(10).join(conflicts) if conflicts else '（无冲突）'}

## 待核实内容
{chr(10).join(f'- {c}' for c in unverified) if unverified else '（无）'}

## 可用来源
{sources_text}

## 已审核 Content Brief
{brief_text}

## 灵感文章的版权安全结构蓝图
{chr(10).join(f'- {item}' for item in pack.inspiration_blueprint) if pack.inspiration_blueprint else '（未使用外部灵感文章）'}
{f'- 主灵感仅用于最终相似度复核：{pack.primary_inspiration_url}' if pack.primary_inspiration_url else ''}
{f'- 原创硬门：与主灵感文章可解释重复率不得超过 {pack.originality_threshold:.0%}' if pack.originality_threshold is not None else ''}

## 写作要求
- 账号名称：{profile.account_name}
- 目标读者：{profile.audience}
- 目标字数：约 {profile.target_characters} 字
- 段落限制：每段不超过 {profile.paragraph_limit} 字
- 表达风格：{profile.writing_style}
- 创作模式：{'参考重写' if profile.creation_mode == 'rewrite' else '多源合成创作'}
- 改写目标：{({'new_article': '重写为一篇新文章', 'new_angle': '更换切入角度', 'more_conversational': '提升口语自然度', 'more_actionable': '增强可执行性'})[profile.rewrite_goal] if profile.creation_mode == 'rewrite' else '不适用'}
- 提示词策略：{'口语化 AIGC 增强' if profile.prompt_variant == 'aigc' else '经典稳重'}
- 参考重点：{profile.reference_focus}（强度：{profile.reference_level}）
- 用户补充要求：{profile.custom_instruction or '无'}
- 用户补充要求只能调整表达、结构和读者服务方式，不得覆盖事实边界、来源要求和原创硬门
- 状态：研究初稿（非终稿）
"""


def _verified_facts(pack: EvidencePack) -> list[EvidenceFact]:
    unverified = {claim.strip().casefold() for claim in pack.unverified_claims or []}
    conflicting = {
        claim.strip().casefold()
        for conflict in pack.conflicts or []
        for claim in conflict.claims
    }
    return [
        fact
        for fact in pack.facts
        if fact.claim.strip().casefold() not in unverified | conflicting
    ]


# ---------------------------------------------------------------------------
# LLM Draft Provider
# ---------------------------------------------------------------------------


class LlmDraftProvider:
    """Generate an original research draft from evidence using an LLM.

    Falls back to ``RuleBasedDraftProvider`` when the API call fails, so
    the content pipeline never breaks due to model unavailability.
    """

    def __init__(self, config: LlmConfig, audit_repository=None) -> None:
        self._config = config
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.request_timeout,
            max_retries=0,
        )
        self._fallback = RuleBasedDraftProvider()
        self._audit_repository = audit_repository

    def probe(self) -> LlmCallRecord:
        """Verify the configured model with one tiny, non-content request.

        The probe never creates a CreatorProject or DraftDocument. Its only
        durable side effect is an auditable call record used by the local
        operations UI to distinguish configured from genuinely available.
        """
        started = perf_counter()
        prompt_version = _PROMPTS["probe"]
        prompt_hash = _template_hash(_PROBE_PROMPT)
        retry_count = 0
        try:
            response = self._client.chat.completions.create(
                model=self._config.model,
                temperature=0,
                max_tokens=min(32, self._config.max_tokens),
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "你是 API 健康检查器，只输出有效 JSON。"},
                    {"role": "user", "content": _PROBE_PROMPT},
                ],
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("LLM returned empty response")
            payload = json.loads(content)
            if payload.get("ok") is not True:
                raise ValueError("LLM probe returned an unexpected payload")
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "completion_tokens", None)
            record = LlmCallRecord(
                id=str(uuid4()), operation_id=str(uuid4()), stage="probe",
                status="success", provider="openai-compatible",
                model=self._config.model, prompt_version_id=prompt_version.id,
                prompt_hash=prompt_hash,
                input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                output_tokens=output_tokens if isinstance(output_tokens, int) else None,
                duration_ms=(perf_counter() - started) * 1000,
                retry_count=retry_count,
            )
        except Exception as exc:
            record = LlmCallRecord(
                id=str(uuid4()), operation_id=str(uuid4()), stage="probe",
                status="error", provider="openai-compatible",
                model=self._config.model, prompt_version_id=prompt_version.id,
                prompt_hash=prompt_hash,
                duration_ms=(perf_counter() - started) * 1000,
                retry_count=retry_count,
                error_code=classify_llm_error(exc),
                fallback_reason=_safe_error_detail(exc),
            )
        self._save_call(record)
        return record

    def generate(
        self,
        pack: EvidencePack,
        profile: WritingProfile,
        *,
        brief: ContentBrief | None = None,
    ) -> DraftDocument:
        return self.generate_with_audit(pack, profile, brief=brief).document

    def generate_with_audit(
        self,
        pack: EvidencePack,
        profile: WritingProfile,
        *,
        brief: ContentBrief | None = None,
        project_id: str | None = None,
        draft_id: str | None = None,
    ) -> GenerationOutcome:
        """Run outline, section and editor stages with one auditable operation."""
        operation_id = str(uuid4())
        call_ids: list[str] = []
        try:
            context = _build_user_prompt(pack, profile, brief)
            outline, call_id = self._call_stage(
                "outline", f"{_OUTLINE_PROMPT}\n\n{context}", operation_id,
                pack, project_id, draft_id,
            )
            call_ids.append(call_id)
            sections, call_id = self._call_stage(
                "section",
                f"{_SECTION_PROMPT}\n\n{context}\n\n已批准大纲：\n{json.dumps(outline, ensure_ascii=False)}",
                operation_id, pack, project_id, draft_id,
            )
            call_ids.append(call_id)
            edited, call_id = self._call_stage(
                "editor",
                f"{_EDITOR_PROMPT}\n\n{context}\n\n大纲：\n{json.dumps(outline, ensure_ascii=False)}\n\n逐节草稿：\n{json.dumps(sections, ensure_ascii=False)}",
                operation_id, pack, project_id, draft_id,
            )
            call_ids.append(call_id)
            if not edited.get("titles") and outline.get("titles"):
                edited["titles"] = outline["titles"]
            document = _parse_llm_response(edited, pack)
            prompt_hash = sha256("".join(_PROMPTS[stage].template_hash for stage in ("outline", "section", "editor")).encode()).hexdigest()
            return GenerationOutcome(
                document=document,
                operation_id=operation_id,
                method="ai",
                status="success",
                model=self._config.model,
                prompt_version_id=_PROMPTS["editor"].id,
                prompt_hash=prompt_hash,
                call_ids=call_ids,
            )
        except Exception as exc:
            fallback = self._fallback.generate_with_audit(
                pack, profile, brief=brief, project_id=project_id, draft_id=draft_id,
                operation_id=operation_id, fallback_reason=classify_llm_error(exc),
            )
            fallback.call_ids = [*call_ids, *fallback.call_ids]
            if self._audit_repository is not None:
                operation_calls = [
                    call.id
                    for call in self._audit_repository.list_calls(100)
                    if call.operation_id == operation_id
                ]
                fallback.call_ids = list(dict.fromkeys([*operation_calls, *fallback.call_ids]))
            return fallback

    def polish_with_audit(
        self,
        pack: EvidencePack,
        profile: WritingProfile,
        document: DraftDocument,
        *,
        brief: ContentBrief | None = None,
        project_id: str | None = None,
        draft_id: str | None = None,
    ) -> GenerationOutcome:
        """Rewrite an existing draft for naturalness without relaxing evidence gates."""
        operation_id = str(uuid4())
        context = _build_user_prompt(pack, profile, brief)
        current = {
            "titles": document.titles,
            "body": document.body,
            "cited_source_urls": document.cited_source_urls,
        }
        edited, call_id = self._call_stage(
            "editor",
            f"{_POLISH_PROMPT}\n\n{context}\n\n当前待润色稿件：\n{json.dumps(current, ensure_ascii=False)}",
            operation_id,
            pack,
            project_id,
            draft_id,
        )
        if not edited.get("titles"):
            edited["titles"] = document.titles
        if not edited.get("cited_source_urls"):
            edited["cited_source_urls"] = document.cited_source_urls
        polished = _parse_llm_response(edited, pack)
        return GenerationOutcome(
            document=polished,
            operation_id=operation_id,
            method="ai",
            status="success",
            model=self._config.model,
            prompt_version_id=_PROMPTS["editor"].id,
            prompt_hash=_template_hash(_POLISH_PROMPT),
            call_ids=[call_id],
        )

    def _call_stage(
        self, stage: str, prompt: str, operation_id: str, pack: EvidencePack,
        project_id: str | None, draft_id: str | None,
    ) -> tuple[dict, str]:
        started = perf_counter()
        prompt_version = _PROMPTS[stage]
        prompt_hash = _template_hash(prompt)
        response = None
        last_error: Exception | None = None
        retry_count = 0
        retry_limit = getattr(self._config, "max_retries", 0)
        retry_limit = retry_limit if isinstance(retry_limit, int) else 0
        for attempt in range(retry_limit + 1):
            retry_count = attempt
            try:
                response = self._client.chat.completions.create(
                    model=self._config.model,
                    temperature=self._config.temperature,
                    max_tokens=self._config.max_tokens,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
                )
                break
            except Exception as exc:
                last_error = exc
                if not _is_retryable_llm_error(exc):
                    break
        if response is not None:
            try:
                content = response.choices[0].message.content
                if not content:
                    raise ValueError("LLM returned empty response")
                data = json.loads(content)
            except Exception as exc:
                record = LlmCallRecord(
                    id=str(uuid4()), operation_id=operation_id, project_id=project_id,
                    draft_id=draft_id, stage=stage, status="error", provider="openai-compatible",
                    model=self._config.model, prompt_version_id=prompt_version.id,
                    prompt_hash=prompt_hash, evidence_claim_ids=[fact.claim_id for fact in pack.facts],
                    duration_ms=(perf_counter() - started) * 1000,
                    retry_count=retry_count, error_code=classify_llm_error(exc),
                    fallback_reason=_safe_error_detail(exc),
                )
                self._save_call(record)
                raise exc
            usage = getattr(response, "usage", None)
            raw_input = getattr(usage, "prompt_tokens", None)
            raw_output = getattr(usage, "completion_tokens", None)
            input_tokens = raw_input if isinstance(raw_input, int) else None
            output_tokens = raw_output if isinstance(raw_output, int) else None
            input_rate = getattr(self._config, "input_cost_per_million", 0)
            output_rate = getattr(self._config, "output_cost_per_million", 0)
            input_rate = input_rate if isinstance(input_rate, (int, float)) else 0
            output_rate = output_rate if isinstance(output_rate, (int, float)) else 0
            estimated_cost = None
            if input_tokens is not None or output_tokens is not None:
                estimated_cost = (
                    (input_tokens or 0) * input_rate
                    + (output_tokens or 0) * output_rate
                ) / 1_000_000
            record = LlmCallRecord(
                id=str(uuid4()), operation_id=operation_id, project_id=project_id,
                draft_id=draft_id, stage=stage, status="success", provider="openai-compatible",
                model=self._config.model, prompt_version_id=prompt_version.id,
                prompt_hash=prompt_hash, evidence_claim_ids=[fact.claim_id for fact in pack.facts],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                duration_ms=(perf_counter() - started) * 1000,
                retry_count=retry_count,
            )
            self._save_call(record)
            return data, record.id
        else:
            exc = last_error or RuntimeError("unknown model failure")
            record = LlmCallRecord(
                id=str(uuid4()), operation_id=operation_id, project_id=project_id,
                draft_id=draft_id, stage=stage, status="error", provider="openai-compatible",
                model=self._config.model, prompt_version_id=prompt_version.id,
                prompt_hash=prompt_hash, evidence_claim_ids=[fact.claim_id for fact in pack.facts],
                duration_ms=(perf_counter() - started) * 1000,
                retry_count=retry_count,
                error_code=classify_llm_error(exc), fallback_reason=_safe_error_detail(exc),
            )
            self._save_call(record)
            raise exc

    def _save_call(self, record: LlmCallRecord) -> None:
        if self._audit_repository is not None:
            self._audit_repository.save_call(record)


def _parse_llm_response(data: dict, pack: EvidencePack) -> DraftDocument:
    """Validate and normalise LLM JSON output into a ``DraftDocument``."""
    titles: list[str] = data.get("titles", [])
    body: str = data.get("body", "")
    cited: list[str] = data.get("cited_source_urls", [])

    # Ensure minimum viable output
    if not titles:
        titles = [pack.topic_title]
    if len(titles) < 5:
        # Pad with angle variations
        base = pack.topic_title
        extras = [
            f"{base}值得跟进吗？先看证据再决定",
            f"别被热度带跑：{base}的价值与边界",
            f"{base}来了，哪些人真正用得上",
            f"拆解{base}：能力、场景与采用成本",
            f"关注{base}之前，先弄清这几个问题",
        ]
        titles = list(dict.fromkeys([*titles, *extras]))[:5]

    # Only keep cited URLs that appear in the evidence pack
    pack_urls = set(pack.source_urls)
    valid_cited = [
        url for url in cited
        if url in pack_urls
    ]
    if not valid_cited and pack.source_urls:
        valid_cited = pack.source_urls[:2]

    return DraftDocument(
        titles=titles[:5],
        body=body or _empty_body_fallback(pack),
        cited_source_urls=valid_cited,
        stage="research",
    )


def _empty_body_fallback(pack: EvidencePack) -> str:
    """Minimal body when the LLM returns nothing usable."""
    facts = pack.facts[:3] if pack.facts else []
    facts_text = "；".join(f.claim for f in facts) if facts else "暂无已核实事实。"
    return (
        f"## 发生了什么\n\n"
        f"围绕「{pack.topic_title}」，当前已核实的信息如下：{facts_text}\n\n"
        f"**注意**：本稿为自动生成的研究初稿，尚未经过人工审核。"
    )


# ---------------------------------------------------------------------------
# Rule-Based Draft Provider (Phase 1 fallback, kept verbatim)
# ---------------------------------------------------------------------------


class RuleBasedDraftProvider:
    """Generate a stable research draft without inventing first-hand experience."""

    def generate(
        self,
        pack: EvidencePack,
        profile: WritingProfile,
        *,
        brief: ContentBrief | None = None,
    ) -> DraftDocument:
        topic = pack.topic_title.strip() or pack.topic_id
        facts = _verified_facts(pack)
        cited_source_urls = _fact_support_urls(facts, pack)
        titles = _titles(topic, pack.category)
        paragraphs = (
            _tool_paragraphs(topic, facts, profile)
            if is_product_category(pack.category)
            else _news_paragraphs(topic, facts, profile)
        )
        if brief is not None:
            paragraphs = _brief_paragraphs(brief, pack, facts)
        body = "\n\n".join(
            segment
            for paragraph in paragraphs
            for segment in _split_paragraph(paragraph, profile.paragraph_limit)
        )
        return DraftDocument(
            titles=titles,
            body=body,
            cited_source_urls=cited_source_urls,
            stage="research",
        )

    def generate_with_audit(
        self, pack: EvidencePack, profile: WritingProfile, *,
        brief: ContentBrief | None = None, project_id: str | None = None,
        draft_id: str | None = None, operation_id: str | None = None,
        fallback_reason: str | None = None,
    ) -> GenerationOutcome:
        operation_id = operation_id or str(uuid4())
        started = perf_counter()
        document = self.generate(pack, profile, brief=brief)
        call = LlmCallRecord(
            id=str(uuid4()), operation_id=operation_id, project_id=project_id,
            draft_id=draft_id, stage="fallback", status="fallback" if fallback_reason else "skipped",
            provider="rule-engine", prompt_version_id=_PROMPTS["fallback"].id,
            prompt_hash=_PROMPTS["fallback"].template_hash,
            evidence_claim_ids=[fact.claim_id for fact in pack.facts],
            duration_ms=(perf_counter() - started) * 1000,
            fallback_reason=fallback_reason or "llm_not_configured",
        )
        audit_repository = getattr(self, "_audit_repository", None)
        if audit_repository is not None:
            audit_repository.save_call(call)
        return GenerationOutcome(
            document=document, operation_id=operation_id, method="rule",
            status="fallback", prompt_version_id=_PROMPTS["fallback"].id,
            prompt_hash=_PROMPTS["fallback"].template_hash, call_ids=[call.id],
        )

    def __init__(self, audit_repository=None) -> None:
        self._audit_repository = audit_repository


def _brief_paragraphs(
    brief: ContentBrief,
    pack: EvidencePack,
    facts: list[EvidenceFact],
) -> list[str]:
    facts_by_id = {fact.claim_id: fact for fact in facts}
    paragraphs: list[str] = [
        brief.core_thesis,
        f"对读者而言，最直接的价值是：{brief.audience_benefit}",
        f"本文采用的判断角度是：{brief.angle}",
        f"与常见信息转述相比，本文的差异在于：{brief.differentiation}",
    ]
    for section in brief.sections:
        paragraphs.append(f"## {section.heading}")
        selected = [
            facts_by_id[claim_id]
            for claim_id in section.claim_ids
            if claim_id in facts_by_id
        ]
        paragraphs.append(section.intent)
        if selected:
            paragraphs.append(f"证据依据：{_fact_paragraph(selected)}")
    if brief.risks:
        paragraphs.extend(
            ["## 证据边界", "；".join(brief.risks)]
        )
    return paragraphs


def _fact_support_urls(
    facts: Iterable[EvidenceFact], pack: EvidencePack
) -> list[str]:
    pack_urls = set(pack.source_urls)
    return sorted(
        {
            url
            for fact in facts
            for url in fact.source_urls
            if url in pack_urls
        }
    )


def _titles(topic: str, category: TopicCategory) -> list[str]:
    if is_product_category(category):
        return [
            f"{topic}值得跟进吗？先看证据再决定",
            f"别被热度带跑：{topic}的价值与边界",
            f"{topic}来了，哪些人真正用得上",
            f"拆解{topic}：能力、场景与采用成本",
            f"关注{topic}之前，先弄清这几个问题",
        ]
    return [
        f"{topic}发生了什么？一文看清重点",
        f"别只看热搜：{topic}真正影响了谁",
        f"{topic}背后，有三个判断值得关注",
        f"读懂{topic}：事实、影响与后续变量",
        f"{topic}之后，行业可能出现哪些变化",
    ]


def _fact_paragraph(facts: list[EvidenceFact]) -> str:
    if not facts:
        return (
            "**证据状态**：目前没有满足交叉验证要求的确定事实，"
            "相关说法仍需补充独立来源后再判断。"
        )
    claims = "；".join(fact.claim.strip() for fact in facts)
    return f"**已核实信息**：{claims}"


def _tool_paragraphs(
    topic: str, facts: list[EvidenceFact], profile: WritingProfile
) -> list[str]:
    return [
        f"围绕{topic}，最容易出现的误区，是只看热度和宣传，却忽略信息有没有可靠出处。",
        (
            "这份内容保持在**研究阶段**，只整理公开证据和可验证判断，"
            "不把推测包装成确定结论。"
        ),
        "## 工具是什么",
        _fact_paragraph(facts),
        (
            "先把事实与评价分开。事实回答它公开了什么能力，评价则要结合"
            "具体任务、流程和使用成本。"
        ),
        (
            "现阶段更稳妥的理解，是把它当作一个待验证的工作选项，"
            "而不是看到热门标签就直接替换原有方案。"
        ),
        "## 为什么会受到关注",
        (
            "工具类内容容易传播，因为读者期待立刻获得效率提升。"
            "但关注度本身，并不能证明它适合每一种工作。"
        ),
        (
            "**真正有价值的问题**不是它有多火，而是它能否减少重复步骤、"
            "降低学习门槛，并保持结果稳定。"
        ),
        (
            "如果公开资料只描述能力，没有说明限制条件，就应把相关判断"
            "保留在观察清单中，等待更多证据。"
        ),
        "## 适合谁关注",
        (
            f"对{profile.audience}来说，优先关注与日常任务直接相关的能力，"
            "比追逐完整功能列表更有效。"
        ),
        (
            "小团队可以先判断它是否解决高频痛点，再看接入成本、协作方式"
            "和结果复核是否可控。"
        ),
        (
            "个人创作者则应关注操作门槛、内容可修改性，以及输出能否保留"
            "自己的表达和判断。"
        ),
        "## 采用前要看什么",
        (
            "**先看来源**：关键能力应能回到官方资料或一手记录，"
            "二次解读只能作为线索，不能替代原始证据。"
        ),
        (
            "**再看场景**：把真实工作拆成输入、处理、复核和交付，"
            "确认工具究竟改善了哪一个环节。"
        ),
        (
            "**最后看边界**：涉及隐私、版权、账号权限和重要决策时，"
            "仍需保留人工检查与责任归属。"
        ),
        (
            "没有明确答案时，可以先建立小范围验证清单，记录目标、过程"
            "和结果，再决定是否扩大使用。"
        ),
        "## 当前判断",
        (
            f"{topic}值得继续观察，但是否采用，应由证据质量和具体需求共同决定，"
            "不能只靠传播热度。"
        ),
        (
            "本稿没有加入个人体验结论。后续若补充可追溯的操作记录，"
            "还需重新检查事实、限制和适用条件。"
        ),
        (
            f"对{profile.account_name}而言，更重要的是持续提供可核验的信息，"
            "帮助读者形成自己的选择，而不是替读者下结论。"
        ),
        (
            "**一句话总结**：先确认来源，再匹配场景，最后用小成本验证，"
            "这是面对任何热门工具都更可靠的顺序。"
        ),
    ]


def _news_paragraphs(
    topic: str, facts: list[EvidenceFact], profile: WritingProfile
) -> list[str]:
    return [
        f"{topic}进入讨论视野后，信息增长很快，观点也很容易跑在事实前面。",
        (
            "这份内容保持在**研究阶段**，先拆分已核实事实、潜在影响"
            "和仍需观察的变量。"
        ),
        "## 发生了什么",
        _fact_paragraph(facts),
        (
            "判断一条消息，首先要确认原始发布者、发布时间和完整语境，"
            "避免只根据转述标题形成结论。"
        ),
        (
            "如果不同来源表达一致，也要继续检查它们是否来自同一个发布者，"
            "防止把重复转载误当成交叉验证。"
        ),
        "## 为什么值得关注",
        (
            "热点的价值不只在消息本身，更在它可能改变产品方向、行业预期"
            "和普通用户的选择成本。"
        ),
        (
            "**短期影响**通常体现在讨论和尝试增加，长期影响则取决于"
            "产品执行、生态配合和真实需求。"
        ),
        (
            "因此，不能把一次发布直接解释为行业终局，也不应因为信息不完整"
            "就忽略已经确认的变化。"
        ),
        "## 谁会受到影响",
        (
            f"对{profile.audience}来说，最直接的问题是现有工具、工作方式"
            "和学习重点是否需要调整。"
        ),
        (
            "企业与团队更关心成本、权限、合规和稳定性，个人用户则更在意"
            "可用性、迁移门槛和实际收益。"
        ),
        (
            "不同群体的判断标准并不相同，所以同一条新闻可能同时带来机会"
            "和新的约束。"
        ),
        "## 现在能下什么结论",
        (
            "**可以确认的部分**应严格对应来源；尚未得到独立支持的说法，"
            "只能作为后续核查线索。"
        ),
        (
            "面对快速变化的信息，保持更新时间和来源记录，比追求一次性"
            "给出完整答案更可靠。"
        ),
        (
            "如果后续出现官方说明、产品文档或当事方记录，应优先用这些材料"
            "修正现有判断。"
        ),
        "## 接下来观察什么",
        (
            "继续观察实际落地、用户反馈和相关规则变化，并区分事实更新"
            "与情绪化解读。"
        ),
        (
            "也要留意关键表述是否发生变化，以及不同来源是否真正独立，"
            "这些细节会影响结论可信度。"
        ),
        (
            f"对{profile.account_name}而言，报道热点的核心不是抢先表态，"
            "而是帮助读者快速看清证据与影响。"
        ),
        (
            f"{topic}仍值得跟进，但当前更合适的动作是保存来源、更新证据，"
            "再根据新事实调整判断。"
        ),
        (
            "**一句话总结**：先确认发生了什么，再判断影响谁，"
            "最后持续跟踪尚未确定的变量。"
        ),
    ]


def _split_paragraph(paragraph: str, limit: int) -> list[str]:
    if paragraph.startswith("## "):
        return [paragraph]
    if len(paragraph.replace("*", "").replace("`", "")) <= limit:
        return [paragraph]

    result: list[str] = []
    remaining = paragraph
    raw_limit = max(limit - 4, 1)
    while len(remaining.replace("*", "").replace("`", "")) > limit:
        boundary = max(
            remaining.rfind(mark, 0, raw_limit)
            for mark in ("，", "。", "；", "、")
        )
        cut = boundary + 1 if boundary >= raw_limit // 2 else raw_limit
        result.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        result.append(remaining)
    return result
