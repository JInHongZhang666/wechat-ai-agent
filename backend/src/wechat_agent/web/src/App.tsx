import { Routes, Route } from 'react-router-dom'
import AppShell from './components/AppShell'
import HomePage from './pages/HomePage'
import AllFeedPage from './pages/AllFeedPage'
import CategoryPage from './pages/CategoryPage'
import DailyPage from './pages/DailyPage'
import HotRankingPage from './pages/HotRankingPage'
import TopicsPage from './pages/TopicsPage'
import TaskListPage from './pages/TaskListPage'
import TaskDetailPage from './pages/TaskDetailPage'
import EvaluationWorkbenchPage from './pages/EvaluationWorkbenchPage'
import CreationWorkbenchPage from './pages/CreationWorkbenchPage'
import ContentOpportunitiesPage from './pages/ContentOpportunitiesPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/all" element={<AllFeedPage />} />
        <Route path="/hot" element={<HotRankingPage />} />
        <Route path="/daily" element={<DailyPage />} />
        <Route path="/topics" element={<TopicsPage />} />
        <Route path="/category/:slug" element={<CategoryPage />} />
        <Route path="/create/tasks" element={<TaskListPage />} />
        <Route path="/create/tasks/:id" element={<TaskDetailPage />} />
        <Route path="/create/topics" element={<ContentOpportunitiesPage />} />
        <Route path="/create/drafts" element={<CreationWorkbenchPage mode="drafts" />} />
        <Route path="/create/quality" element={<CreationWorkbenchPage mode="quality" />} />
        <Route path="/ops/evaluation" element={<EvaluationWorkbenchPage />} />
      </Route>
    </Routes>
  )
}
