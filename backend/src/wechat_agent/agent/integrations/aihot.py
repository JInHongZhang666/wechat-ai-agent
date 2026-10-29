"""Isolated AIHOT v1 adapter for recent public AI-news discovery."""

from __future__ import annotations

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from wechat_agent.collectors.http import HttpGetter, RetryingHttpGetter


AIHOT_ITEMS_URL = 'https://aihot.news/api/v1/items'
AIHOT_USER_AGENT = 'aihot-api/2.0.0'


class AihotSearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    query: str = Field(default='', max_length=200)
    window: Literal['24h', '7d'] = '7d'
    category: Literal['ai-models', 'ai-products', 'industry', 'paper', 'tip', 'opinion'] | None = None
    mode: Literal['selected', 'all'] = 'selected'
    limit: int = Field(default=8, ge=1, le=10)

    @field_validator('query')
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = value.strip()
        if value and len(value) < 2:
            raise ValueError('query must be empty or contain at least two characters')
        return value


class AihotResearchExtension:
    """One-tool extension; delete this module and its registration to remove AIHOT."""

    definitions = {
        'search_aihot_news': (
            AihotSearchInput,
            '查询AIHOT最近24小时或7天的公开中文AI资讯。适合最新公司、模型、产品、行业、论文、教程或观点；'
            '默认返回精选内容。它是外部发现来源，不等于独立事实核验，返回ID不能用于本地build_research_evidence。',
        ),
    }

    def __init__(self, *, client: HttpGetter | None = None, timeout_seconds: float = 8.0):
        self._owned_client: httpx.Client | None = None
        if client is None:
            self._owned_client = httpx.Client(
                timeout=timeout_seconds,
                follow_redirects=False,
                headers={'User-Agent': AIHOT_USER_AGENT, 'Accept': 'application/json'},
            )
            client = RetryingHttpGetter(self._owned_client, max_attempts=2)
        self._client = client

    def execute(self, name: str, arguments) -> dict:
        if name not in self.definitions:
            return {'ok': False, 'error_code': 'UNKNOWN_TOOL', 'retryable': False}
        try:
            args = AihotSearchInput.model_validate(arguments)
        except ValidationError:
            return {'ok': False, 'error_code': 'INVALID_TOOL_ARGUMENTS', 'retryable': True}

        params: dict[str, str | int] = {
            'mode': args.mode,
            'window': args.window,
            'by': 'timeline',
            'limit': args.limit,
        }
        if args.query:
            params['q'] = args.query
        if args.category:
            params['category'] = args.category

        try:
            response = self._client.get(
                AIHOT_ITEMS_URL,
                params=params,
                headers={'User-Agent': AIHOT_USER_AGENT, 'Accept': 'application/json'},
            )
            if response.status_code == 429 or response.status_code >= 500:
                return {'ok': False, 'error_code': 'AIHOT_UPSTREAM_UNAVAILABLE', 'retryable': True}
            if response.status_code != 200:
                return {'ok': False, 'error_code': 'AIHOT_REQUEST_REJECTED', 'retryable': False}
            payload = response.json()
            raw_items = payload.get('items') if isinstance(payload, dict) else None
            if not isinstance(raw_items, list):
                raise ValueError('missing items')
            items = [self._normalize(item) for item in raw_items[:args.limit]]
        except (httpx.HTTPError, TypeError, ValueError, KeyError):
            return {'ok': False, 'error_code': 'AIHOT_INVALID_RESPONSE', 'retryable': True}

        return {
            'ok': True,
            'tool': name,
            'error_code': None,
            'data': {
                'scope': 'aihot_recent_public_items',
                'provider': 'AIHOT',
                'query': args.query,
                'window': args.window,
                'category': args.category,
                'mode': args.mode,
                'matched_count': len(items),
                'items': items,
                'limitation': 'AIHOT用于近期资讯发现；摘要和推荐理由不能替代原文核验或独立信源检查。',
            },
        }

    @staticmethod
    def _normalize(item: object) -> dict:
        if not isinstance(item, dict):
            raise ValueError('invalid item')
        links = item.get('links')
        source = item.get('source')
        if not isinstance(links, dict) or not isinstance(source, dict):
            raise ValueError('invalid links or source')
        required = (item.get('id'), item.get('title'), links.get('aihot'), links.get('original'),
                    source.get('name'), item.get('discoveredAt'))
        if not all(isinstance(value, str) and value for value in required):
            raise ValueError('missing required item field')
        return {
            'id': f"aihot:{item['id']}",
            'title': item['title'][:300],
            'summary': str(item.get('summary') or '')[:700],
            'url': links['original'],
            'aihot_url': links['aihot'],
            'published_at': item.get('publishedAt') or item['discoveredAt'],
            'discovered_at': item['discoveredAt'],
            'source': source['name'][:200],
            'category': item.get('category'),
            'selected': bool(item.get('selected')),
            'score': item.get('score'),
            'reason': str(item.get('reason') or '')[:300],
            'provenance': 'external_aggregator',
        }

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()
