import type { AppState } from '../../App';

type Props = { state: AppState; setPartial: (p: Partial<AppState>) => void };

export default function TopicPool({ state, setPartial }: Props) {
  if (!state.topics.length) {
    return (
      <aside className="panel">
        <div className="panel-head"><h3>候选选题</h3><small>尚未生成</small></div>
        <div className="p-[42px_22px] text-center text-muted leading-relaxed">
          点击"运行今日真实雷达"<br />生成今天的 5+5 候选。
        </div>
      </aside>
    );
  }

  const groups = [
    ['tool', 'A组 · GitHub与实用AI工具'],
    ['news', 'B组 · AI热点时讯'],
  ] as const;

  return (
    <aside className="panel">
      <div className="panel-head"><h3>候选选题</h3><small>{state.topics.length} 项</small></div>
      <div className="max-h-[720px] overflow-auto max-lg:max-h-[420px]">
        {groups.map(([category, label]) => {
          const items = state.topics.filter((t) => t.category === category);
          return (
            <div key={category}>
              <div className="sticky top-0 z-[2] py-2.5 px-4 text-muted bg-paper/95 backdrop-blur text-[11px] font-extrabold tracking-[1px]">
                {label} · {items.length}
              </div>
              {items.map((topic) => (
                <button
                  key={topic.id}
                  onClick={() => setPartial({ activeTopic: topic, evidence: null, draft: null })}
                  className={`w-full py-[15px] px-4 border-0 border-b border-[#ece8df] bg-transparent text-left text-inherit hover:bg-green-soft ${
                    state.activeTopic?.id === topic.id ? 'bg-green-soft' : ''
                  }`}
                >
                  <strong className="block leading-snug mb-2">{topic.title}</strong>
                  <span className="flex items-center justify-between text-muted text-[11px]">
                    <span>{topic.heat_score.confidence === 'measured' ? '有量化信号' : '估算信号'}</span>
                    <span className="text-green font-black">{topic.heat_score.value.toFixed(1)} 分</span>
                  </span>
                </button>
              ))}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
