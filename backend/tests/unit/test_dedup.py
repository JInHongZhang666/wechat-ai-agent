from datetime import UTC, datetime

from wechat_agent.domain.models import ArticleRecord, TopicCategory
from wechat_agent.topics.dedup import canonicalize_url, deduplicate


NOW = datetime(2026, 7, 25, 12, tzinfo=UTC)


def article(article_id: str, *, title: str, url: str) -> ArticleRecord:
    return ArticleRecord(
        id=article_id,
        title=title,
        source_name="test source",
        source_url=url,
        published_at=NOW,
        collected_at=NOW,
        summary="test summary",
        category=TopicCategory.NEWS,
    )


def test_canonicalize_url_strips_tracking_parameters_and_fragment():
    url = "HTTPS://Example.COM/article?b=2&utm_source=wechat&gclid=abc&a=1#section"

    assert canonicalize_url(url) == "https://example.com/article?a=1&b=2"


def test_canonicalize_url_preserves_non_analytics_parameters():
    url = (
        "https://example.com/article?"
        "source=alpha&from=feed&ref=home&ref_topic=ai&dclid=content-key"
    )

    assert canonicalize_url(url) == (
        "https://example.com/article?"
        "dclid=content-key&from=feed&ref=home&ref_topic=ai&source=alpha"
    )


def test_deduplicate_keeps_first_article_for_matching_canonical_urls():
    first = article(
        "first",
        title="A launch",
        url="https://example.com/post?utm_campaign=launch",
    )
    duplicate = article("duplicate", title="Other title", url="https://example.com/post")

    assert deduplicate([first, duplicate]) == [first]


def test_deduplicate_does_not_merge_urls_with_distinct_source_parameters():
    first = article(
        "first",
        title="First article",
        url="https://example.com/post?id=7&source=alpha",
    )
    second = article(
        "second",
        title="Second article",
        url="https://example.com/post?id=7&source=beta",
    )

    assert deduplicate([first, second]) == [first, second]


def test_deduplicate_then_collapses_identical_normalized_titles():
    first = article("first", title="New AI Tool!", url="https://example.com/one")
    duplicate = article("duplicate", title=" new-ai tool ", url="https://example.com/two")

    assert deduplicate([first, duplicate]) == [first]
