import TopicPool from '../topics/TopicPool';
import EvidencePanel from '../evidence/EvidencePanel';
import WritingDesk from '../writing/WritingDesk';
import type { AppState } from '../../App';

type Props = { state: AppState; setPartial: (p: Partial<AppState>) => void };

export default function Workspace({ state, setPartial }: Props) {
  if (!state.task) {
    return (
      <div className="grid grid-cols-[360px_minmax(0,1fr)] gap-[18px] items-start max-lg:grid-cols-1">
        <div className="panel empty-state">点击上方按钮开始</div>
        <div className="panel empty-state">选择左侧选题后查看详情</div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-[360px_minmax(0,1fr)] gap-[18px] items-start max-lg:grid-cols-1">
      <TopicPool state={state} setPartial={setPartial} />
      {state.activeTopic ? (
        state.draft ? (
          <WritingDesk state={state} setPartial={setPartial} />
        ) : (
          <EvidencePanel state={state} setPartial={setPartial} />
        )
      ) : (
        <div className="panel">
          <div className="panel-head">
            <h3>研究台</h3>
            <small className="text-muted">选择左侧选题</small>
          </div>
          <div className="p-6 text-center text-muted leading-relaxed min-h-[640px] flex items-center justify-center">
            <div>
              这里会展示热度依据、来源和证据缺口。<br />
              证据不足时仍可生成研究稿，但不能冒充已核验终稿。
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
