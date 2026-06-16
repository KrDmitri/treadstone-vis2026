import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import FeedPage from './pages/FeedPage'
import DataPage from './pages/DataPage'
import SettingsPage from './pages/SettingsPage'

function App() {
  return (
    <Router
      future={{
        v7_startTransition: true,
        v7_relativeSplatPath: true,
      }}
    >
      <Routes>
        <Route path="/" element={<FeedPage />} />
        <Route path="/data" element={<DataPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </Router>
  )
}

export default App

