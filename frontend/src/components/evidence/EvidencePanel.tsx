import { useEffect, useState } from 'react';
import type { AppState } from '../../App';
import type { EvidencePack } from '../../types/api';
import { api, ApiClientError } from '../../api/client';

type Props = { state: AppState; setPartial: (p: Partial<AppState>) => void };

function safeUrl(url: string): string {
  try { const u = new URL(url); return ['http:', 'https:'].includes(u.protocol) ? u.href : '#'; }
  catch { return '#'; }
}

function sourceLabel(url: string, evidence: EvidencePack, index: number): string {
  for (const fact of evidence.facts) {
    const src = fact.sources?.[url];
    if (src) return src.source_name || `来源 ${index + 1}`;
  }
  return `来源 ${index + 1}`;
}

export default function EvidencePanel({ state, setPartial }: Props) {
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    if (!state.activeTopic || state.evidence) return;
    setPartial({ loading: '正在生成证据包' });
    api.getEvidence(state.activeTopic.id)
      .then((evidence) => setPartial({ evidence, loading: '' }))
      .catch((err) => setPartial({ loading: '', error: err instanceof ApiClientError ? err.message : '获取证据失败' }));
  }, [state.activeTopic?.id]);

  if (!state.evidence) {
    return (
      <article className="panel">
        <div className="panel-head"><h3>{state.activeTopic?.title || '研究台'}</h3><small>加载中</small></div>
        <div className="p-6 text-center text-muted">{state.loading || '加载中…'}</div>
      </article>
    );
  }

  const { activeTopic: topic, evidence } = state;
  if (!topic) return null;

  async function generateDraft() {
    setGenerating(true);
    try {
      const draft = await api.createDraft(topic!.id);
      setPartial({ draft });
    } catch (err) {
      setPartial({ error: err instanceof ApiClientError ? err.message : '生成失败' });
    } finally {
      setGenerating(false);
    }
  }

  return (
    <article className="panel min-h-[640px]">
      <div className="panel-head">
        <h3>{topic.title}</h3>
        <small>{topic.category === 'tool' ? '工具选题' : '热点时讯'}</small>
      </div>
      <div className="p-6">
        <div className="grid grid-cols-4 gap-2.5 mb-5 max-sm:grid-cols-2">
          <div className="p-3.5 bg-[#f2eee5] rounded-xl">
            <span className="block text-muted text-[11px] mb-1.5">热度分</span>
            <strong className="text-lg">{topic.heat_score.value.toFixed(1)}</strong>
          </div>
          <div className="p-3.5 bg-[#f2eee5] rounded-xl">
            <span className="block text-muted text-[11px] mb-1.5">来源数</span>
            <strong className="text-lg">{evidence.source_urls.length}</strong>
          </div>
          <div className="p-3.5 bg-[#f2eee5] rounded-xl">
            <span className="block text-muted text-[11px] mb-1.5">事实卡</span>
            <strong className="text-lg">{evidence.facts.length}</strong>
          </div>
          <div className="p-3.5 bg-[#f2eee5] rounded-xl">
            <span className="block text-muted text-[11px] mb-1.5">状态</span>
            <strong className="text-lg">{evidence.ready_for_draft ? '可核验' : '研究中'}</strong>
          </div>
        </div>

        <div className="mb-5 mt-5 text-muted text-xs font-extrabold tracking-[1px]">推荐理由</div>
        <div className="py-3 px-3.5 border-l-[3px] border-green rounded-r-xl bg-[#f4f6f1] leading-relaxed">{topic.recommendation_reason}</div>

        <div className="mb-2.5 mt-5 text-muted text-xs font-extrabold tracking-[1px]">证据事实</div>
        {evidence.facts.length ? evidence.facts.map((fact, i) => (
          <div key={i} className="py-3 px-3.5 mb-2 border-l-[3px] border-green rounded-r-xl bg-[#f4f6f1] leading-relaxed">
            {fact.claim}
            <small className="block mt-1.5">
              {fact.source_urls.map((url, j) => (
                <span key={url}>
                  {j > 0 && ' · '}
                  <a className="text-green no-underline hover:underline" href={safeUrl(url)} target="_blank" rel="noreferrer">
                    {Object.values(fact.sources)[j]?.source_name || `来源`}
                  </a>
                </span>
              ))}
            </small>
          </div>
        )) : <div className="py-3 px-3.5 text-red">尚未形成可交叉核验的事实卡。</div>}

        <div className="mb-2.5 mt-5 text-muted text-xs font-extrabold tracking-[1px]">直接来源</div>
        <div>
          {evidence.source_urls.length ? evidence.source_urls.map((url, i) => (
            <a key={url} className="inline-flex items-center gap-1 mr-2 mb-2 py-1.5 px-2.5 rounded-lg bg-[#edf4ef] text-green text-xs no-underline hover:underline" href={safeUrl(url)} target="_blank" rel="noreferrer">
              {sourceLabel(url, evidence, i)} ↗
            </a>
          )) : <span className="text-red">暂无来源</span>}
        </div>

        {!!evidence.unverified_claims?.length && (
          <>
            <div className="mb-2.5 mt-5 text-red text-xs font-extrabold tracking-[1px]">待核实内容</div>
            {evidence.unverified_claims.map((claim, i) => (
              <div key={i} className="py-3 px-3.5 mb-2 border-l-[3px] border-red rounded-r-xl bg-[#fff0d9] leading-relaxed text-red">{claim}</div>
            ))}
          </>
        )}

        <div className="mt-5">
          <button
            onClick={generateDraft}
            disabled={generating}
            className="border-0 rounded-xl py-3 px-[18px] font-extrabold text-white bg-green transition-transform hover:-translate-y-px disabled:cursor-wait disabled:opacity-60"
          >
            {generating ? '正在生成研究稿…' : '生成研究版初稿'}
          </button>
        </div>
      </div>
    </article>
  );
}
