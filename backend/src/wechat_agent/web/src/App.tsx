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
import VideoAnalysisPage from './pages/VideoAnalysisPage'
import MembershipPage from './pages/MembershipPage'
import WechatPublishingPage from './pages/WechatPublishingPage'
import PromptLibraryPage from './pages/PromptLibraryPage'
import CreatorSettingsPage from './pages/CreatorSettingsPage'
import AgentChatPage from './pages/AgentChatPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/all" element={<AllFeedPage />} />
        <Route path="/hot" element={<HotRankingPage />} />
        <Route path="/daily" element={<DailyPage />} />
        <Route path="/topics" element={<TopicsPage />} />
        <Route path="/agent" element={<AgentChatPage />} />
        <Route path="/category/:slug" element={<CategoryPage />} />
        <Route path="/create/tasks" element={<TaskListPage />} />
        <Route path="/create/tasks/:id" element={<TaskDetailPage />} />
        <Route path="/create/topics" element={<ContentOpportunitiesPage />} />
        <Route path="/create/drafts" element={<CreationWorkbenchPage mode="drafts" />} />
        <Route path="/create/quality" element={<CreationWorkbenchPage mode="quality" />} />
        <Route path="/create/video" element={<VideoAnalysisPage />} />
        <Route path="/create/membership" element={<MembershipPage />} />
        <Route path="/create/wechat" element={<WechatPublishingPage />} />
        <Route path="/create/prompts" element={<PromptLibraryPage />} />
        <Route path="/create/settings" element={<CreatorSettingsPage />} />
        <Route path="/create/settings/:section" element={<CreatorSettingsPage />} />
        <Route path="/ops/evaluation" element={<EvaluationWorkbenchPage />} />
      </Route>
    </Routes>
  )
}
