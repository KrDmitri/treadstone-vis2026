import { useState, useRef, useCallback } from 'react'
import LeftSidebar from '../components/LeftSidebar'
import TopNavBar from '../components/TopNavBar'
import MainFeed from '../components/MainFeed'
import AnalysisHub from '../components/AnalysisHub'

function FeedPage() {
  const [activeView, setActiveView] = useState('feed')
  const [activeNodeId, setActiveNodeId] = useState(null)
  const [hubWidth, setHubWidth] = useState(384)
  const isDragging = useRef(false)
  const containerRef = useRef(null)

  const onMouseDown = useCallback(() => {
    isDragging.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMouseMove = (e) => {
      if (!isDragging.current || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const newWidth = rect.right - e.clientX
      const maxWidth = rect.width * 0.85
      setHubWidth(Math.max(160, Math.min(maxWidth, newWidth)))
    }

    const onMouseUp = () => {
      isDragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [])

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      <TopNavBar activeView={activeView} onViewChange={setActiveView} />
      <div className="flex flex-1 overflow-hidden" ref={containerRef}>
        <MainFeed
          communicationMode="sns"
          activeView={activeView}
          onViewChange={setActiveView}
          activeNodeId={activeNodeId}
          onNodeActive={setActiveNodeId}
        />

        {/* Resizable divider */}
        <div
          onMouseDown={onMouseDown}
          className="w-1 bg-gray-200 hover:bg-blue-400 cursor-col-resize flex-shrink-0 transition-colors"
        />

        <div
          style={{ width: hubWidth, flexShrink: 0 }}
          className="h-full overflow-hidden"
        >
          <AnalysisHub activeNodeId={activeNodeId} onNodeActive={setActiveNodeId} />
        </div>
      </div>
    </div>
  )
}

export default FeedPage
