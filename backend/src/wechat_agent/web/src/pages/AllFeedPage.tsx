import FeedPage from './FeedPage'

export default function AllFeedPage() {
  return (
    <FeedPage
      mode="all"
      title="全部 AI 动态"
      subtitle="查看通过预处理并完成质量评估的全部公开动态"
      searchable
    />
  )
}
