import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import Button from '../components/Button'
import Timeline from '../components/Timeline'
import { useApi } from '../hooks/useApi'
import { apiGet, apiPost } from '../utils/api'
import type { Task, TimelineItem } from '../types'

export default function TaskListPage() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [network, setNetwork] = useState<SourceNetworkStatus | null>(null)
  const { loading, execute } = useApi<Task>()
  const navigate = useNavigate()

  useEffect(() => {
    try {
      const stored = localStorage.getItem('ai-cabin-tasks')
      if (stored) setTasks(JSON.parse(stored))
    } catch { /* empty */ }
  }, [])

  useEffect(() => {
    let cancelled = false
    const refresh = async () => {
      try {
        const value = await apiGet<SourceNetworkStatus>('/v1/sources/network-status')
        if (!cancelled) setNetwork(value)
      } catch { /* collection controls still work without diagnostics */ }
    }
    void refresh()
    const timer = window.setInterval(refresh, 30_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const createDemoTask = async () => {
    try {
      const task = await execute(apiPost<Task>('/tasks/demo'))
      const updated = [task, ...tasks]
      setTasks(updated)
      localStorage.setItem('ai-cabin-tasks', JSON.stringify(updated))
      navigate(`/create/tasks/${task.id}`)
    } catch {
      alert('演示任务创建失败，请确保后端服务已启动。')
    }
  }

  const createLiveTask = async () => {
    try {
      const task = await execute(apiPost<Task>('/tasks/live'))
      const updated = [task, ...tasks]
      setTasks(updated)
      localStorage.setItem('ai-cabin-tasks', JSON.stringify(updated))
      navigate(`/create/tasks/${task.id}`)
    } catch {
      alert('真实数据采集暂不可用，请先使用 Demo 模式。')
    }
  }

  const taskItems: TimelineItem[] = tasks.map(task => ({
    id: task.id,
    title: `${task.mode === 'demo' ? '🎭 Demo' : '🌐 Live'} · 采集 ${task.collected_article_count || '...'} 篇文章`,
    originalTitle: null,
    summary: task.status === 'failed'
      ? '任务执行失败，请重试。'
      : task.status === 'completed'
        ? '所有选题均已完成草稿并通过质量检查 ✅'
        : task.status === 'review_ready'
          ? '选题已生成，可开始创建草稿。'
          : `状态: ${task.status}`,
    source: { name: `模式: ${task.mode}`, tier: task.mode === 'live' ? 'T1' : 'T2' },
    links: {
      original: `/create/tasks/${task.id}`,
      aihot: `/create/tasks/${task.id}`,
    },
    publishedAt: null,
    discoveredAt: new Date().toISOString(),
    timelineAt: new Date().toISOString(),
    category: task.status === 'completed' ? 'ai-models' : 'ai-products',
    score: task.status === 'completed' ? 100 : task.status === 'failed' ? 0 : 50,
    selected: task.status === 'completed',
  }))

  return (
    <>
      <div className="page-header-compact" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 className="page-title">✏️ AI 创作任务</h1>
          <p className="page-subtitle">创建采集任务，自动生成选题、证据包与研究草稿</p>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <Button variant="primary" onClick={createDemoTask} disabled={loading}>
            🎭 Demo 任务
          </Button>
          <Button onClick={createLiveTask} disabled={loading}>
            🌐 实时采集
          </Button>
        </div>
      </div>

      {network && (
        <section className={`source-network-banner is-${network.state}`}>
          <div>
            <span>SOURCE NETWORK</span>
            <strong>{network.state === 'normal' ? '采集网络正常' : network.state === 'recovering' ? '采集网络正在分批恢复' : '检测到全局网络异常'}</strong>
            <small>
              调度器{network.schedulerRunning ? '运行中' : '未运行'} · {network.enabledSourceCount} 个来源 · {network.openCircuitCount} 个熔断
            </small>
          </div>
          <p>
            {network.state === 'normal'
              ? `最近成功：${formatStatusTime(network.lastSuccessAt)}`
              : `系统每批只探测 ${network.probeBatchSize} 个来源，避免同时重试；下次探测：${formatStatusTime(network.nextProbeAt)}`}
          </p>
        </section>
      )}

      {tasks.length === 0 && !loading ? (
        <div className="page-empty-state">
          <div className="page-empty-state-icon">📋</div>
          <p className="page-empty-state-text">
            还没有任务。<br />
            点击「Demo 任务」使用演示数据快速体验，或「实时采集」连接公开数据源。
          </p>
        </div>
      ) : (
        <Timeline items={taskItems} loading={loading} />
      )}
    </>
  )
}

interface SourceNetworkStatus {
  state: 'normal' | 'outage' | 'recovering'
  schedulerRunning: boolean
  enabledSourceCount: number
  openCircuitCount: number
  probeBatchSize: number
  nextProbeAt: string | null
  lastSuccessAt: string | null
}

function formatStatusTime(value: string | null): string {
  if (!value) return '暂无'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}
