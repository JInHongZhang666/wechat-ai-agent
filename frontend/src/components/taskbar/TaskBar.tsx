import type { AppState } from '../../App';
import { api, ApiClientError } from '../../api/client';

type Props = { state: AppState; setPartial: (p: Partial<AppState>) => void };

export default function TaskBar({ state, setPartial }: Props) {
  const busy = !!state.loading;
  const statusLabel = state.task
    ? state.task.status === 'review_ready' || state.task.status === 'completed'
      ? '雷达已就绪'
      : state.task.status === 'failed'
      ? '运行失败'
      : '采集与评分中'
    : '等待运行';

  async function run(mode: 'demo' | 'live') {
    setPartial({ loading: mode === 'live' ? '正在检索公开来源' : '正在载入演示数据', error: null });
    try {
      const task = await api.createTask(mode);
      const { items, shortage } = await api.getTopics(task.id);
      setPartial({ task, topics: items, shortage, loading: '', activeTopic: null, evidence: null, draft: null });
    } catch (err) {
      setPartial({
        loading: '',
        error: err instanceof ApiClientError ? err.message : '运行失败',
        task: state.task ? { ...state.task, status: 'failed', status_history: [...state.task.status_history, 'failed'] } : null,
      });
    }
  }

  return (
    <section className="grid grid-cols-[minmax(0,1.4fr)_minmax(300px,.6fr)] overflow-hidden border border-green/15 rounded-[28px] bg-surface shadow-card mb-[18px] max-lg:grid-cols-1">
      <div className="p-[42px] max-sm:p-7">
        <div className="text-green text-xs font-extrabold tracking-[2px] uppercase mb-3.5">Daily Topic Radar</div>
        <h2 className="max-w-[720px] m-0 text-[clamp(32px,5vw,60px)] leading-tight tracking-[-2px] max-sm:tracking-[-1px]">
          从公开信号里，找到<span className="text-green">今天值得写</span>的题。
        </h2>
        <p className="max-w-[660px] mt-5 mb-6 text-muted leading-relaxed">
          自动检索近期 GitHub 项目、官方 AI 公告与公开公众号索引，按互动速度和发布时间筛选 5 个工具选题与 5 个 AI 时讯。无法核验的数据会降置信度，不补造"10万+"。
        </p>
        <div className="flex flex-wrap gap-3">
          <button
            onClick={() => run('live')}
            disabled={busy}
            className="border-0 rounded-xl py-3 px-[18px] font-extrabold transition-transform hover:-translate-y-px disabled:cursor-wait disabled:opacity-60 disabled:transform-none text-white bg-green"
          >
            {state.loading === '正在检索公开来源' ? '正在检索公开来源…' : '运行今日真实雷达'}
          </button>
          <button
            onClick={() => run('demo')}
            disabled={busy}
            className="border-0 rounded-xl py-3 px-[18px] font-extrabold transition-transform hover:-translate-y-px disabled:cursor-wait disabled:opacity-60 disabled:transform-none text-ink bg-[#eee9dd]"
          >
            {state.loading === '正在载入演示数据' ? '正在载入演示数据…' : '使用演示数据'}
          </button>
        </div>
      </div>
      <aside className="min-h-[250px] p-8 text-[#f7f3e9] bg-ink flex flex-col justify-between max-lg:min-h-[180px] max-sm:p-6">
        <div>
          <strong className="block text-lg mb-2.5">当前工作流</strong>
          <p className="text-[#b9c5c0] text-[13px] leading-relaxed m-0">
            {state.task?.mode === 'live' ? '公开采集 → 去重评分 → 5+5 选题 → AI写作 → 语义验证 → 质量闸门' : '公开采集 → 去重评分 → 5+5 选题 → 证据包 → 研究稿 → 质量闸门'}
          </p>
        </div>
        <span className="self-start inline-flex items-center gap-2 py-2 px-[11px] rounded-full bg-white/10 text-xs">
          <span className="w-2 h-2 rounded-full bg-[#75e0b4] shadow-[0_0_0_5px_rgba(117,224,180,.1)]" />
          {statusLabel}
        </span>
      </aside>
    </section>
  );
}
