import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  failed: boolean
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('页面渲染失败', error, info)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="page-empty-state">
        <div className="page-empty-state-icon">⚠️</div>
        <p className="page-empty-state-text">页面暂时无法显示，请刷新后重试。</p>
        <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
          刷新页面
        </button>
      </div>
    )
  }
}
