"""Quick smoke test for DeepSeek LLM writing integration."""
import os
from wechat_agent.config import LlmConfig
from wechat_agent.writing.provider import LlmDraftProvider
from wechat_agent.domain.models import (
    EvidencePack, EvidenceFact, EvidenceSource,
    SourceType, TopicCategory, WritingProfile,
)

def main():
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    print(f"Key 状态: {'已设置' if api_key else '未设置'}")

    sources = {
        "https://openai.com/news/test": EvidenceSource(
            source_name="OpenAI", source_identity="openai",
            source_type=SourceType.OFFICIAL, source_type_verified=True,
            provenance="OpenAI 官方 RSS",
        ),
        "https://github.blog/ai/test": EvidenceSource(
            source_name="GitHub Blog", source_identity="github",
            source_type=SourceType.OFFICIAL, source_type_verified=True,
            provenance="GitHub AI Blog RSS",
        ),
    }
    fact = EvidenceFact(
        claim="GPT-4o 支持 50 种语言，响应速度提升 2 倍。",
        source_urls=list(sources.keys()),
        sources=sources,
    )
    pack = EvidencePack(
        topic_id="test-1", topic_title="GPT-4o 正式发布多语言支持",
        category=TopicCategory.NEWS, facts=[fact],
        source_urls=list(sources.keys()),
        suggested_angle="从多语言能力和实际应用场景解读这次更新。",
        ready_for_draft=True,
    )

    config = LlmConfig(api_key=api_key)
    provider = LlmDraftProvider(config)
    profile = WritingProfile(account_name="AI信息小屋")

    print("⏳ 调用 DeepSeek API...")
    draft = provider.generate(pack, profile)
    print(f"\n✅ 成功！标题数: {len(draft.titles)}, 正文字数: {len(draft.body)}")
    for i, t in enumerate(draft.titles, 1):
        print(f"  {i}. {t}")
    print(f"\n正文 (前 200 字):\n{draft.body[:200]}...")
    print(f"来源: {draft.cited_source_urls}")

if __name__ == "__main__":
    main()
