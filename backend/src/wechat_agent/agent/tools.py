"""M2 research adapters with explicitly opted-in, due-source collection."""
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from wechat_agent.domain.models import TopicCandidate, HeatScore, HeatConfidence
from wechat_agent.evidence.build import build_evidence_pack


class SearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    query: str = Field(default='', max_length=200)
    window: Literal['24h', '7d', '30d'] = '7d'
    limit: int = Field(default=5, ge=1, le=10)


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    article_ids: list[str] = Field(min_length=1, max_length=5)


class SupportingInput(SearchInput):
    article_id: str
    query: str = Field(min_length=2, max_length=200)


class ResearchTools:
    definitions = {
        'search_ai_news': (SearchInput, '按关键词查询本地已采集公开AI资讯；不是实时联网搜索。query为空可浏览。window控制发布时间范围。空结果不代表全网没有新闻。'),
        'find_supporting_sources': (SupportingInput, '为已查询的article_id检索本地补充来源。query使用具体事件关键词；结果只是候选，不代表事实已核实。'),
        'build_research_evidence': (EvidenceInput, '用本次任务已经查询到的同一事件文章ID构建摘要证据包。返回事实、来源、冲突和缺口。不得传入自编事实或URL。'),
        'refresh_news_sources': (EmptyInput, '仅在用户允许时刷新已配置且到期的信源，每个任务最多尝试一次。不是任意网址抓取或全网关键词搜索；刷新后需重新查询。'),
    }

    def __init__(self, service, *, allow_refresh=False, extensions=()):
        self.service = service
        self.allow_refresh = allow_refresh
        self.seen = {}
        self.refresh_result = None
        self.extensions = {}
        self.definitions = dict(type(self).definitions)
        for extension in extensions:
            for name, definition in extension.definitions.items():
                if name in self.definitions:
                    raise ValueError(f'duplicate research tool: {name}')
                self.definitions[name] = definition
                self.extensions[name] = extension

    def _evidence(self, args):
        ids = list(dict.fromkeys(args.article_ids))
        if any(i not in self.seen for i in ids):
            return {'ok': False, 'error_code': 'ARTICLE_NOT_OBSERVED', 'retryable': True}
        articles = [self.seen[i] for i in ids]
        first = articles[0]
        topic = TopicCandidate(id=first.id, title=first.title, category=first.category,
            summary=first.summary, recommendation_reason='Agent选择的研究候选，尚需证据检查',
            heat_score=HeatScore(value=0, confidence=HeatConfidence.ESTIMATED))
        pack = build_evidence_pack(topic, articles)
        publishers = {s.source_identity for f in pack.facts for s in f.sources.values()}
        gaps = []
        if not pack.facts:
            gaps.append('NO_FACTS')
        if len(publishers) < 2:
            gaps.append('INSUFFICIENT_INDEPENDENT_SOURCES')
        if not any(s.source_type_verified and s.source_type.value in {'official', 'primary'}
                   for f in pack.facts for s in f.sources.values()):
            gaps.append('MISSING_VERIFIED_PRIMARY_SOURCE')
        if pack.unverified_claims:
            gaps.append('UNVERIFIED_CLAIMS')
        if pack.conflicts:
            gaps.append('CONFLICTING_CLAIMS')
        data = pack.model_dump(mode='json')
        data.update(article_ids=ids, independent_source_count=len(publishers), gaps=gaps,
                    evidence_ready=pack.ready_for_draft and not gaps,
                    scope='collected_summaries', assessment='deterministic_summary_matching',
                    limitation='摘要匹配不等于原文全面核验，不能据此自动发布。')
        # Keep context bounded, but derive the decision from the full evidence pack.
        data['fact_count'] = len(data['facts'])
        data['facts'] = data['facts'][:20]
        data['unverified_claims'] = data['unverified_claims'][:20]
        data['conflicts'] = data['conflicts'][:10]
        return {'ok': True, 'tool': 'build_research_evidence', 'data': data, 'error_code': None}

    def _refresh(self):
        if not self.allow_refresh:
            return {'ok': False, 'error_code': 'REFRESH_NOT_ALLOWED', 'retryable': False}
        if self.refresh_result is not None:
            return {**self.refresh_result, 'reused': True}
        # Reserve the attempt before a potentially side-effecting operation.
        self.refresh_result = {'ok': False, 'error_code': 'REFRESH_FAILED', 'retryable': False}
        method = getattr(self.service, 'create_scheduled_live_task', None)
        if method is None or getattr(self.service, 'live_collector', None) is None:
            self.refresh_result = {'ok': False, 'error_code': 'REFRESH_UNAVAILABLE', 'retryable': False}
            return self.refresh_result
        try:
            task = method()
            self.refresh_result = {'ok': True, 'tool': 'refresh_news_sources', 'error_code': None,
                'data': {'scope': 'configured_due_sources', 'task_id': task.id if task else None,
                         'collected_article_count': task.collected_article_count if task else 0,
                         'warnings': list(getattr(task, 'collection_warnings', []))[:10] if task else [],
                         'status': 'refreshed' if task else 'no_due_sources',
                         'next_action': '重新查询并构建证据，采集数量不能证明事实已核验。'}}
        except Exception:
            pass
        return self.refresh_result

    def schemas(self):
        return [
            {'type': 'function', 'function': {
                'name': name, 'description': description,
                'parameters': model.model_json_schema(),
            }} for name, (model, description) in self.definitions.items()
        ]

    def execute(self, name, arguments):
        if name in self.extensions:
            return self.extensions[name].execute(name, arguments)
        if name not in self.definitions:
            return {'ok': False, 'error_code': 'UNKNOWN_TOOL', 'retryable': False}
        try:
            args = self.definitions[name][0].model_validate(arguments)
        except ValidationError:
            return {'ok': False, 'error_code': 'INVALID_TOOL_ARGUMENTS', 'retryable': True}
        try:
            if name == 'build_research_evidence':
                return self._evidence(args)
            if name == 'refresh_news_sources':
                return self._refresh()
            if name == 'find_supporting_sources' and args.article_id not in self.seen:
                return {'ok': False, 'error_code': 'ARTICLE_NOT_OBSERVED', 'retryable': True}
            now = datetime.now(UTC)
            days = {'24h': 1, '7d': 7, '30d': 30}[args.window]
            # Use the same eligible Article + CurationDecision pool as public feeds.
            rows = self.service.pipeline.list_distribution_items(
                now - timedelta(days=days), limit=10000,
            )
            def matches_query(article):
                text = (article.title + ' ' + article.summary).casefold()
                if name == 'find_supporting_sources':
                    normalized = ''.join(text.split())
                    return all(term in normalized for term in args.query.casefold().split())
                return args.query.strip().casefold() in text
            matches = [a for a, decision in rows
                       if decision.eligible
                       and now - timedelta(days=days) <= a.published_at <= now
                       and matches_query(a)]
            matches.sort(key=lambda a: (a.published_at, a.id), reverse=True)
            if name == 'find_supporting_sources':
                origin = self.seen[args.article_id]
                matches = [a for a in matches if a.source_url != origin.source_url]
            for article in matches[:args.limit]:
                self.seen[article.id] = article
            data = {'scope': 'local_collected_articles', 'window': args.window,
                    'query': args.query, 'scan_limit': 10000,
                    'scan_may_be_truncated': len(rows) >= 10000,
                    'matched_count': len(matches), 'items': [
                        {'id': a.id, 'title': a.title[:300], 'summary': a.summary[:1200],
                         'url': a.source_url, 'published_at': a.published_at.isoformat(),
                         'source': a.source_name, 'source_type': a.source_type.value,
                         'source_type_verified': a.source_type_verified,
                         'source_identity': a.source_identity} for a in matches[:args.limit]]}
            return {'ok': True, 'tool': name, 'data': data, 'error_code': None}
        except Exception:
            # Provider/storage exception strings can contain credentials or local paths.
            return {'ok': False, 'error_code': 'TOOL_EXECUTION_FAILED', 'retryable': False}

    def close(self):
        for extension in dict.fromkeys(self.extensions.values()):
            close = getattr(extension, 'close', None)
            if close is not None:
                close()
