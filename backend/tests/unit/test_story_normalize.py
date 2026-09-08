from tests.story_factories import make_story_article
from wechat_agent.story.models import story_pair_key
from wechat_agent.story.normalize import normalize_article


def test_normalize_article_preserves_versions_and_removes_noise():
    article = make_story_article(
        title="重磅｜Qwen 3.5-35B 正式发布！ - 量子位",
        source_url="https://Example.com/news/?utm_source=rss&id=7#top",
    )

    normalized = normalize_article(article)

    assert normalized.normalized_url == "https://example.com/news?id=7"
    assert "3.5" in normalized.versions
    assert "qwen" in normalized.normalized_title
    assert "重磅" not in normalized.normalized_title
    assert article.title == "重磅｜Qwen 3.5-35B 正式发布！ - 量子位"


def test_story_pair_key_is_order_independent():
    assert story_pair_key("article-a", "article-b") == story_pair_key(
        "article-b", "article-a"
    )
