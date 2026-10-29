"""AIHOT stays an optional extension and never requires live network in tests."""

from types import SimpleNamespace

from wechat_agent.agent.integrations.aihot import (
    AIHOT_ITEMS_URL,
    AIHOT_USER_AGENT,
    AihotResearchExtension,
)
from wechat_agent.agent.tools import ResearchTools


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


class Getter:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, *, params=None, headers=None):
        self.calls.append((url, params, headers))
        return self.response


def item(**updates):
    value = {
        'id': 'item-1',
        'title': 'Claude 发布新模型',
        'originalTitle': 'Claude releases a model',
        'summary': '一条用于测试的摘要。',
        'source': {'name': 'Anthropic'},
        'links': {'aihot': 'https://aihot.news/read/item-1',
                  'original': 'https://example.com/claude'},
        'publishedAt': '2026-10-26T01:00:00Z',
        'discoveredAt': '2026-10-26T02:00:00Z',
        'category': 'ai-models',
        'score': 91,
        'selected': True,
        'reason': '值得关注',
    }
    value.update(updates)
    return value


def test_aihot_maps_public_json_without_leaking_api_details():
    getter = Getter(Response({'schemaVersion': 1, 'items': [item()], 'page': {}}))
    extension = AihotResearchExtension(client=getter)

    result = extension.execute('search_aihot_news', {
        'query': 'Claude', 'window': '24h', 'category': 'ai-models',
        'mode': 'selected', 'limit': 5,
    })

    assert result['ok'] is True
    assert result['data']['scope'] == 'aihot_recent_public_items'
    assert result['data']['items'][0]['id'] == 'aihot:item-1'
    assert result['data']['items'][0]['url'] == 'https://example.com/claude'
    url, params, headers = getter.calls[0]
    assert url == AIHOT_ITEMS_URL
    assert params == {'mode': 'selected', 'window': '24h', 'by': 'timeline',
                      'limit': 5, 'q': 'Claude', 'category': 'ai-models'}
    assert headers['User-Agent'] == AIHOT_USER_AGENT


def test_aihot_rejects_bad_arguments_before_http():
    getter = Getter(Response({'items': []}))
    extension = AihotResearchExtension(client=getter)
    result = extension.execute('search_aihot_news', {'window': '30d'})
    assert result['error_code'] == 'INVALID_TOOL_ARGUMENTS'
    assert getter.calls == []


def test_aihot_upstream_failure_is_redacted_and_retryable():
    extension = AihotResearchExtension(client=Getter(Response({}, status_code=503)))
    result = extension.execute('search_aihot_news', {})
    assert result == {'ok': False, 'error_code': 'AIHOT_UPSTREAM_UNAVAILABLE',
                      'retryable': True}


def test_extension_is_registered_only_when_injected():
    service = SimpleNamespace()
    local = ResearchTools(service)
    extended = ResearchTools(
        service,
        extensions=[AihotResearchExtension(client=Getter(Response({'items': []})))],
    )
    assert 'search_aihot_news' not in local.definitions
    assert 'search_aihot_news' in extended.definitions
