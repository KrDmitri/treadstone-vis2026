import { useState, useEffect } from 'react'
import SummaryTab from './tabs/SummaryTab'
import NotificationTab from './tabs/NotificationTab'
import { useWebSocket } from '../hooks/useWebSocket'

const LAST_READ_KEY = 'treadstone_activity_last_read'

function AnalysisHub({ activeNodeId, onNodeActive }) {
  const [activeTab, setActiveTab] = useState('summary')
  const [unreadCount, setUnreadCount] = useState(0)

  // WebSocket connection
  const { lastMessage, clientId, isConnected } = useWebSocket()


  // Load lastReadTime from localStorage
  const [lastReadTime, setLastReadTime] = useState(() => {
    const saved = localStorage.getItem(LAST_READ_KEY)
    return saved ? parseInt(saved, 10) : 0
  })

  // Simple: Increment unread count when new agent message arrives
  useEffect(() => {

    if (!lastMessage) return

    // Only count agent messages (new_reply or new_post from agents)
    if (lastMessage.type === 'new_reply' || lastMessage.type === 'new_post') {
      const author = lastMessage.reply?.author || lastMessage.post?.author || ''

      // Skip user messages
      if (author !== 'user' && author !== 'system' && author !== '') {
        // Only increment if not on Activity tab
        if (activeTab !== 'notifications') {
          setUnreadCount(prev => {
            return prev + 1
          })
        }
      }
    }
  }, [lastMessage, activeTab])

  const tabs = [
    { id: 'summary', name: 'Summary', icon: '📋' },
    { id: 'notifications', name: 'Activity', icon: '🔔' },
  ]

  const handleTabClick = (tabId) => {
    setActiveTab(tabId)
    if (tabId === 'notifications') {
      // Mark all as read
      setUnreadCount(0)
      const now = Date.now()
      setLastReadTime(now)
      localStorage.setItem(LAST_READ_KEY, now.toString())
    }
  }

  return (
    <div className="w-full h-full bg-white border-l border-gray-200 flex flex-col">
      {/* Header */}
      <div className="p-6 border-b border-gray-200">
        <h2 className="text-xl font-semibold text-gray-800">Analysis Hub</h2>
        <p className="text-xs text-gray-500 mt-1">Sensemaking Space</p>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => handleTabClick(tab.id)}
            className={`relative flex-1 px-4 py-3 text-sm font-medium transition-colors ${activeTab === tab.id
              ? 'text-blue-600 border-b-2 border-blue-600'
              : 'text-gray-600 hover:text-gray-800'
              }`}
          >
            <div className="flex items-center justify-center">
              <span className="mr-1">{tab.icon}</span>
              {tab.name}
              {tab.id === 'notifications' && unreadCount > 0 && (
                <span className="ml-1.5 flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-red-500 text-[10px] font-bold text-white shadow-sm px-1 animate-pulse">
                  {unreadCount > 99 ? '99+' : unreadCount}
                </span>
              )}
            </div>
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="flex-1 overflow-hidden relative">
        <div className={`absolute inset-0 overflow-y-auto p-6 ${activeTab === 'summary' ? 'block' : 'hidden'}`}>
          <SummaryTab />
        </div>
        <div className={`absolute inset-0 overflow-y-auto p-6 ${activeTab === 'notifications' ? 'block' : 'hidden'}`}>
          <NotificationTab
            lastReadTime={lastReadTime}
            isActive={activeTab === 'notifications'}
            wsMessage={lastMessage}
            clientId={clientId}
            activeNodeId={activeNodeId}
            onNodeActive={onNodeActive}
          />
        </div>
      </div>
    </div>
  )
}

export default AnalysisHub
