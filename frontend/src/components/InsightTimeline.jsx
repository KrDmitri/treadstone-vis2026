import React, { useState, useEffect, useRef, useMemo } from 'react'
import axios from 'axios'
import { getClientId } from '../utils/clientId'

/**
 * InsightTimeline - Shows tagged items with dual connection lines
 * Left: Structural (Post-Reply hierarchy)
 * Right: Semantic (LLM-inferred connections)
 */

// Tag configuration
const TAG_CONFIG = {
  hypothesis: { icon: '🎯', color: 'bg-purple-100 text-purple-700 border-purple-200', label: 'Hypothesis' },
  evidence: { icon: '📊', color: 'bg-blue-100 text-blue-700 border-blue-200', label: 'Evidence' },
  question: { icon: '❓', color: 'bg-orange-100 text-orange-700 border-orange-200', label: 'Question' },
  insight: { icon: '💡', color: 'bg-yellow-100 text-yellow-700 border-yellow-200', label: 'Insight' },
  todo: { icon: '☐', color: 'bg-green-100 text-green-700 border-green-200', label: 'To-Do' }
}

// Connection type configuration
const CONNECTION_CONFIG = {
  supports: { icon: '✅', color: 'text-green-600', bgColor: 'bg-green-50', lineColor: '#16a34a', label: 'supports' },
  contradicts: { icon: '❌', color: 'text-red-600', bgColor: 'bg-red-50', lineColor: '#dc2626', label: 'contradicts' },
  answers: { icon: '💬', color: 'text-blue-600', bgColor: 'bg-blue-50', lineColor: '#2563eb', label: 'answers' },
  extends: { icon: '➕', color: 'text-purple-600', bgColor: 'bg-purple-50', lineColor: '#9333ea', label: 'extends' },
  questions: { icon: '❓', color: 'text-orange-600', bgColor: 'bg-orange-50', lineColor: '#ea580c', label: 'questions' }
}

// Agent role to icon mapping
const AGENT_ICONS = {
  statistics: '📊',
  visualization: '📈', 
  insight: '💼',
  summary: '📝',
  scanner: '🔍',
  user: '👤'
}

function InsightTimeline({ onViewChange, onNavigateToPost }) {
  const clientId = getClientId()
  const [items, setItems] = useState([])
  const [tagCounts, setTagCounts] = useState({})
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selectedTags, setSelectedTags] = useState(new Set())
  const [expandedItems, setExpandedItems] = useState(new Set())
  const [highlightedItem, setHighlightedItem] = useState(null)
  const [hoveredItem, setHoveredItem] = useState(null) // Unified hover state
  const itemRefs = useRef({})
  const containerRef = useRef(null)

  // Group items by post_id for structural view
  const groupedByPost = useMemo(() => {
    const groups = new Map()
    items.forEach(item => {
      const postId = item.post_id
      if (!groups.has(postId)) {
        groups.set(postId, { post: null, replies: [] })
      }
      if (item.type === 'post') {
        groups.get(postId).post = item
      } else {
        groups.get(postId).replies.push(item)
      }
    })
    return groups
  }, [items])

  // Build semantic connection map
  const connectionMap = useMemo(() => {
    const map = new Map()
    items.forEach(item => {
      if (item.connections && item.connections.length > 0) {
        item.connections.forEach(conn => {
          if (!map.has(item.id)) map.set(item.id, [])
          map.get(item.id).push({ 
            targetId: conn.target_id, 
            relation: conn.relation, 
            confidence: conn.confidence,
            direction: 'outgoing' 
          })
          
          if (!map.has(conn.target_id)) map.set(conn.target_id, [])
          map.get(conn.target_id).push({ 
            targetId: item.id, 
            relation: conn.relation, 
            confidence: conn.confidence,
            direction: 'incoming' 
          })
        })
      }
    })
    return map
  }, [items])

  // Check if item is semantically connected to hovered item
  const isSemanticConnected = (itemId) => {
    if (!hoveredItem) return false
    if (itemId === hoveredItem.id) return true
    const connections = connectionMap.get(hoveredItem.id) || []
    return connections.some(c => c.targetId === itemId)
  }

  // Check if item is structurally related (same post group)
  const isStructuralConnected = (item) => {
    if (!hoveredItem) return false
    return item.post_id === hoveredItem.post_id
  }

  // Get the relation type to hovered item
  const getConnectionRelation = (itemId) => {
    if (!hoveredItem) return null
    const connections = connectionMap.get(hoveredItem.id) || []
    const conn = connections.find(c => c.targetId === itemId)
    return conn ? conn.relation : null
  }

  useEffect(() => {
    fetchTimeline()
    const interval = setInterval(fetchTimeline, 10000)
    return () => clearInterval(interval)
  }, [clientId])

  const fetchTimeline = async () => {
    try {
      const response = await axios.get('/api/timeline', {
        params: { client_id: clientId }
      })
      setItems(response.data.items || [])
      setTagCounts(response.data.tag_counts || {})
      setError(null)
    } catch (err) {
      console.error('Failed to fetch timeline:', err)
      setError('Failed to load timeline')
    } finally {
      setIsLoading(false)
    }
  }

  const filteredItems = selectedTags.size === 0 
    ? items 
    : items.filter(item => item.tags?.some(t => selectedTags.has(t)))

  const toggleTag = (tag) => {
    setSelectedTags(prev => {
      const newSet = new Set(prev)
      if (newSet.has(tag)) newSet.delete(tag)
      else newSet.add(tag)
      return newSet
    })
  }

  const toggleExpand = (itemId) => {
    setExpandedItems(prev => {
      const newSet = new Set(prev)
      if (newSet.has(itemId)) newSet.delete(itemId)
      else newSet.add(itemId)
      return newSet
    })
  }

  const scrollToItem = (itemId) => {
    const element = itemRefs.current[itemId]
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setHighlightedItem(itemId)
      setTimeout(() => setHighlightedItem(null), 2000)
    }
  }

  const navigateToPost = (postId, replyId = null) => {
    if (onNavigateToPost) {
      onNavigateToPost(postId, replyId)
    }
  }

  const getAuthorIcon = (authorType, authorRole) => {
    if (authorType === 'user') return '👤'
    return AGENT_ICONS[authorRole] || '🤖'
  }

  const formatTime = (dateString) => {
    if (!dateString) return ''
    return new Date(dateString).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="flex-1 flex flex-col">
        <div className="bg-white border-b border-gray-200 px-6 py-4">
          <h2 className="text-xl font-semibold text-gray-800">Insight Timeline</h2>
          <p className="text-sm text-gray-500">Structural & Semantic Connections</p>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-gray-500">
            <span className="text-4xl animate-pulse">📅</span>
            <p className="mt-2">Loading timeline...</p>
          </div>
        </div>
      </div>
    )
  }

  // Empty state
  if (items.length === 0) {
    return (
      <div className="flex-1 flex flex-col">
        <div className="bg-white border-b border-gray-200 px-6 py-4">
          <h2 className="text-xl font-semibold text-gray-800">Insight Timeline</h2>
          <p className="text-sm text-gray-500">Structural & Semantic Connections</p>
        </div>
        <div className="flex-1 flex items-center justify-center bg-gray-50">
          <div className="text-center text-gray-500">
            <span className="text-6xl">📅</span>
            <p className="mt-4 text-lg font-medium">No insights yet</p>
            <p className="text-sm">Start analyzing data in the Feed to see insights here.</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold text-gray-800">Insight Timeline</h2>
            <p className="text-sm text-gray-500">
              Thread Structure & Semantic Links
            </p>
          </div>
        </div>
      </div>

      {/* Tag Filters */}
      <div className="bg-white border-b border-gray-100 px-6 py-3 flex-shrink-0">
        <div className="flex flex-wrap gap-2">
          {Object.entries(TAG_CONFIG).map(([tag, config]) => {
            const count = tagCounts[tag] || 0
            const isSelected = selectedTags.has(tag)
            return (
              <button
                key={tag}
                onClick={() => toggleTag(tag)}
                className={`px-3 py-1 rounded-full text-sm border transition-all ${
                  isSelected
                    ? config.color + ' ring-2 ring-offset-1 ring-gray-300'
                    : 'bg-gray-100 text-gray-600 border-gray-200 hover:bg-gray-200'
                }`}
              >
                {config.label} ({count})
              </button>
            )
          })}
        </div>
      </div>

      {/* Legend */}
      <div className="bg-gray-50 border-b border-gray-200 px-6 py-2 flex-shrink-0">
        <div className="flex items-center gap-6 text-xs text-gray-500">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-gray-400" />
            <span>Left: Post-Reply Thread</span>
          </div>
          <div className="flex items-center gap-4">
            <span>Right:</span>
            {Object.entries(CONNECTION_CONFIG).slice(0, 4).map(([key, config]) => (
              <div key={key} className="flex items-center gap-1">
                <div className="w-2 h-2 rounded-full" style={{ backgroundColor: config.lineColor }} />
                <span>{config.label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Timeline with dual lines */}
      <div className="flex-1 overflow-y-auto bg-gray-50 p-6" ref={containerRef}>
        <div className="max-w-4xl mx-auto">
          {filteredItems.map((item, index) => {
            const isExpanded = expandedItems.has(item.id)
            const isHighlighted = highlightedItem === item.id
            const isThisHovered = hoveredItem?.id === item.id
            const isSemanticHighlighted = isSemanticConnected(item.id)
            const isStructuralHighlighted = isStructuralConnected(item)
            const isAnyHighlighted = isThisHovered || isSemanticHighlighted || isStructuralHighlighted
            const hasConnections = item.connections && item.connections.length > 0
            const primaryTag = item.tags?.[0]
            const tagConfig = TAG_CONFIG[primaryTag] || TAG_CONFIG.insight
            const isReply = item.type === 'reply'
            const itemConnections = connectionMap.get(item.id) || []
            const connectionRelation = getConnectionRelation(item.id)
            
            // Check if next item is in same post (for structural line)
            const nextItem = filteredItems[index + 1]
            const hasSamePostNext = nextItem && nextItem.post_id === item.post_id

            return (
              <div
                key={item.id}
                ref={el => itemRefs.current[item.id] = el}
                className={`relative transition-all duration-300 flex ${
                  isHighlighted || isAnyHighlighted ? 'scale-[1.01]' : ''
                } ${hoveredItem && !isAnyHighlighted ? 'opacity-40' : 'opacity-100'}`}
                onMouseEnter={() => setHoveredItem(item)}
                onMouseLeave={() => setHoveredItem(null)}
              >
                {/* LEFT SIDE: Structural Connection (Post-Reply) */}
                <div className="w-16 flex-shrink-0 relative">
                  {/* Vertical line for same-post items - only show when hovered */}
                  {hasSamePostNext && (
                    <div 
                      className={`absolute left-3 top-10 w-1 h-full transition-all rounded-full ${
                        isStructuralHighlighted 
                          ? 'bg-indigo-500 opacity-100' 
                          : hoveredItem ? 'bg-gray-200 opacity-30' : 'bg-gray-200 opacity-50'
                      }`}
                    />
                  )}
                  
                  {/* Post indicator - prominent */}
                  {!isReply && (
                    <div className={`
                      flex items-center gap-1 mt-3 transition-all cursor-pointer
                      ${isStructuralHighlighted ? 'scale-110' : ''}
                    `}>
                      <div className={`
                        w-7 h-7 rounded-lg flex items-center justify-center text-xs font-bold transition-all
                        ${isStructuralHighlighted 
                          ? 'bg-indigo-500 text-white ring-2 ring-indigo-300 shadow-lg' 
                          : 'bg-indigo-100 text-indigo-700 border border-indigo-300'
                        }
                      `}>
                        P{item.post_id}
                      </div>
                    </div>
                  )}
                  
                  {/* Reply indicator - indented */}
                  {isReply && (
                    <div className="flex items-center mt-3">
                      {/* Horizontal connector line - more visible on hover */}
                      <div className={`w-3 h-0.5 transition-all ${
                        isStructuralHighlighted ? 'bg-indigo-500 h-1' : 'bg-gray-300'
                      }`} />
                      {/* Reply badge */}
                      <div className={`
                        w-6 h-6 rounded flex items-center justify-center text-xs
                        transition-all cursor-pointer
                        ${isStructuralHighlighted 
                          ? 'bg-indigo-200 text-indigo-700 ring-2 ring-indigo-300 font-bold' 
                          : 'bg-gray-100 text-gray-500'
                        }
                      `}>
                        ↳
                      </div>
                    </div>
                  )}
                </div>

                {/* CENTER: Content Card */}
                <div 
                  className={`flex-1 mb-3 bg-white rounded-lg shadow-sm border transition-all cursor-pointer ${
                    isThisHovered
                      ? 'border-blue-500 ring-2 ring-blue-200 shadow-lg'
                      : isSemanticHighlighted
                        ? 'border-green-400 ring-2 ring-green-100 shadow-md'
                        : isStructuralHighlighted
                          ? 'border-indigo-400 ring-2 ring-indigo-100 shadow-md'
                          : hasConnections
                            ? 'border-blue-200 hover:border-blue-300'
                            : 'border-gray-200 hover:border-gray-300'
                  }`}
                  onClick={() => toggleExpand(item.id)}
                >
                  {/* Card Header */}
                  <div className="px-4 py-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span>{getAuthorIcon(item.author_type, item.author_role)}</span>
                        <span className="font-medium text-gray-800 text-sm">{item.author}</span>
                        {item.tags?.map(tag => (
                          <span 
                            key={tag}
                            className={`px-2 py-0.5 text-xs rounded-full border ${TAG_CONFIG[tag]?.color || 'bg-gray-100'}`}
                          >
                            {TAG_CONFIG[tag]?.label || tag}
                          </span>
                        ))}
                      </div>
                      <div className="flex items-center gap-2 text-xs text-gray-400">
                        <span>{formatTime(item.created_at)}</span>
                        <span className={`transition-transform ${isExpanded ? 'rotate-180' : ''}`}>▼</span>
                      </div>
                    </div>
                    
                    <p className={`mt-2 text-sm text-gray-600 ${isExpanded ? '' : 'line-clamp-2'}`}>
                      {item.content}
                    </p>

                    {/* Connection summary when collapsed */}
                    {!isExpanded && hasConnections && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {item.connections.slice(0, 3).map((conn, idx) => {
                          const connConfig = CONNECTION_CONFIG[conn.relation] || CONNECTION_CONFIG.extends
                          return (
                            <span
                              key={idx}
                              className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full ${connConfig.bgColor} ${connConfig.color}`}
                            >
                              {connConfig.icon} {connConfig.label}
                            </span>
                          )
                        })}
                        {item.connections.length > 3 && (
                          <span className="text-xs text-gray-400">+{item.connections.length - 3}</span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Expanded Content */}
                  {isExpanded && (
                    <div className="px-4 pb-4 border-t border-gray-100">
                      {/* Outgoing connections */}
                      {hasConnections && (
                        <div className="mt-3">
                          <p className="text-xs font-medium text-gray-500 mb-2">
                            🔗 Connects to ({item.connections.length})
                          </p>
                          <div className="space-y-1">
                            {item.connections.map((conn, idx) => {
                              const connConfig = CONNECTION_CONFIG[conn.relation] || CONNECTION_CONFIG.extends
                              return (
                                <button
                                  key={idx}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    scrollToItem(conn.target_id)
                                  }}
                                  className={`w-full text-left px-3 py-2 rounded-lg ${connConfig.bgColor} hover:opacity-80 transition-all flex items-center gap-2`}
                                >
                                  <span className={connConfig.color}>{connConfig.icon}</span>
                                  <span className={`text-sm font-medium ${connConfig.color}`}>{connConfig.label}</span>
                                  <span className="text-sm text-gray-500 truncate flex-1">"{conn.target_summary}..."</span>
                                  <span className="text-xs text-gray-400">{Math.round(conn.confidence * 100)}%</span>
                                  <span className="text-gray-400">→</span>
                                </button>
                              )
                            })}
                          </div>
                        </div>
                      )}
                      
                      {/* Incoming connections */}
                      {itemConnections.filter(c => c.direction === 'incoming').length > 0 && (
                        <div className="mt-3">
                          <p className="text-xs font-medium text-gray-500 mb-2">
                            ⬅️ Referenced by ({itemConnections.filter(c => c.direction === 'incoming').length})
                          </p>
                          <div className="flex flex-wrap gap-1">
                            {itemConnections.filter(c => c.direction === 'incoming').map((conn, idx) => {
                              const connConfig = CONNECTION_CONFIG[conn.relation] || CONNECTION_CONFIG.extends
                              return (
                                <button
                                  key={idx}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    scrollToItem(conn.targetId)
                                  }}
                                  className={`inline-flex items-center gap-1 px-2 py-1 text-xs rounded-lg ${connConfig.bgColor} ${connConfig.color} hover:opacity-80`}
                                >
                                  ← {connConfig.icon} {conn.relation}
                                </button>
                              )
                            })}
                          </div>
                        </div>
                      )}

                      {/* Actions */}
                      <div className="mt-3 flex items-center gap-2">
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            navigateToPost(item.post_id, item.reply_id)
                          }}
                          className="px-3 py-1.5 text-xs bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 transition-colors"
                        >
                          📍 View in Feed
                        </button>
                      </div>
                    </div>
                  )}
                </div>

                {/* RIGHT SIDE: Semantic Connection Indicators - only visible on hover */}
                <div className={`w-24 flex-shrink-0 relative pl-2 transition-opacity duration-200 ${
                  hoveredItem ? 'opacity-100' : 'opacity-0'
                }`}>
                  {/* Outgoing connections */}
                  {hasConnections && isThisHovered && (
                    <div className="absolute top-2 left-2 flex flex-col gap-1">
                      {item.connections.map((conn, idx) => {
                        const connConfig = CONNECTION_CONFIG[conn.relation] || CONNECTION_CONFIG.extends
                        return (
                          <div
                            key={idx}
                            className={`flex items-center gap-1 px-2 py-1 rounded-lg text-xs cursor-pointer 
                              hover:scale-105 transition-all shadow-sm border ${connConfig.bgColor}`}
                            style={{ borderColor: connConfig.lineColor }}
                            onClick={(e) => {
                              e.stopPropagation()
                              scrollToItem(conn.target_id)
                            }}
                          >
                            <div 
                              className="w-2.5 h-2.5 rounded-full" 
                              style={{ backgroundColor: connConfig.lineColor }}
                            />
                            <span className={`${connConfig.color} font-medium`}>
                              {connConfig.label}
                            </span>
                            <span className="text-gray-400">→</span>
                          </div>
                        )
                      })}
                    </div>
                  )}
                  
                  {/* Show indicator when this item is connected to hovered item */}
                  {isSemanticHighlighted && !isThisHovered && connectionRelation && (
                    <div className="absolute top-2 left-2">
                      <div 
                        className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs shadow-md animate-pulse"
                        style={{ 
                          backgroundColor: CONNECTION_CONFIG[connectionRelation]?.color || '#ddd',
                          color: CONNECTION_CONFIG[connectionRelation]?.textColor || '#333'
                        }}
                      >
                        <span className="font-bold">←</span>
                        <span>{CONNECTION_CONFIG[connectionRelation]?.label}</span>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default InsightTimeline
