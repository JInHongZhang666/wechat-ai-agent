import FeedPage from './FeedPage'

export default function HomePage() {
  const today = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }).format(new Date())

  return (
    <FeedPage
      mode="selected"
      title="精选"
      subtitle={`${today} · AI 自动挑选的高价值内容`}
      searchable
      showCategories
      showHotTopics
    />
  )
}
