import { useState, useCallback } from 'react';
import Masthead from './components/layout/Masthead';
import TaskBar from './components/taskbar/TaskBar';
import Workspace from './components/layout/Workspace';
import type { TaskPayload, TopicCandidate, EvidencePack, DraftPayload } from './types/api';

export type AppState = {
  task: TaskPayload | null;
  topics: TopicCandidate[];
  shortage: Record<string, number>;
  activeTopic: TopicCandidate | null;
  evidence: EvidencePack | null;
  draft: DraftPayload | null;
  loading: string;
  error: string | null;
};

const INITIAL: AppState = {
  task: null,
  topics: [],
  shortage: {},
  activeTopic: null,
  evidence: null,
  draft: null,
  loading: '',
  error: null,
};

export default function App() {
  const [state, setState] = useState<AppState>(INITIAL);

  const setPartial = useCallback(
    (patch: Partial<AppState>) => setState((prev) => ({ ...prev, ...patch })),
    [],
  );

  return (
    <main className="mx-auto w-[min(1480px,calc(100%-32px))] py-7 pb-14">
      <Masthead />
      <TaskBar state={state} setPartial={setPartial} />
      <Workspace state={state} setPartial={setPartial} />
    </main>
  );
}
