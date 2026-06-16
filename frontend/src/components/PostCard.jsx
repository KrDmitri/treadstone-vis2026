import { useState, useEffect, useRef } from 'react'
import { Vega } from 'react-vega'
import ReactMarkdown from 'react-markdown'
import axios from 'axios'
import { getClientId } from '../utils/clientId'

// Role icons for display (used when we recognize a role keyword)
const ROLE_ICONS = {
  'statistical': '📊',
  'statistics': '📊',
  'visualization': '📈',
  'viz': '📈',
  'insight': '💡',
  'intelligence': '💡',
  'summary': '📝',
  'summarize': '📝',
  'narrator': '📝',
  'scout': '🔍',
  'scanner': '🔍',
}

// Function to get icon for a mention (returns generic icon for unknown mentions)
function getMentionIcon(mentionText) {
  const lowerText = mentionText.toLowerCase()
  for (const [key, icon] of Object.entries(ROLE_ICONS)) {
    if (lowerText.includes(key)) {
      return icon
    }
  }
  return '🤖' // Default icon for custom agents
}

// Function to render text with highlighted @mentions and #post references
// Detects @word pattern for agents and #N pattern for post references
function renderTextWithMentions(text) {
  if (!text || typeof text !== 'string') return text

  // Collect all mentions and references
  const highlights = []

  // Regex for @mentions - ONLY match word directly attached to @ (no spaces)
  const mentionRegex = /@(\w+)/g
  let match
  while ((match = mentionRegex.exec(text)) !== null) {
    highlights.push({
      start: match.index,
      end: match.index + match[0].length,
      text: match[0],
      type: 'mention',
      icon: getMentionIcon(match[0])
    })
  }

  // Regex for #post references (#1, #2, etc.)
  const postRefRegex = /#(\d+)/g
  while ((match = postRefRegex.exec(text)) !== null) {
    highlights.push({
      start: match.index,
      end: match.index + match[0].length,
      text: match[0],
      type: 'post_ref',
      icon: '📎'
    })
  }

  if (highlights.length === 0) {
    return text
  }

  // Sort by position and remove overlaps
  highlights.sort((a, b) => a.start - b.start)

  // Build segments
  const segments = []
  let lastEnd = 0

  for (const highlight of highlights) {
    // Skip if overlapping with previous
    if (highlight.start < lastEnd) continue

    // Text before highlight
    if (highlight.start > lastEnd) {
      segments.push(text.slice(lastEnd, highlight.start))
    }

    // Styled highlight
    if (highlight.type === 'mention') {
      segments.push(
        <span
          key={`mention-${highlight.start}`}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 mx-0.5 bg-gradient-to-r from-blue-100 to-indigo-100 text-blue-700 rounded-md font-medium text-xs border border-blue-200 shadow-sm"
        >
          <span>{highlight.icon}</span>
          <span>{highlight.text}</span>
        </span>
      )
    } else if (highlight.type === 'post_ref') {
      segments.push(
        <span
          key={`postref-${highlight.start}`}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 mx-0.5 bg-gradient-to-r from-violet-100 to-purple-100 text-violet-700 rounded-md font-medium text-xs border border-violet-200 shadow-sm cursor-pointer hover:from-violet-200 hover:to-purple-200 transition-colors"
          title={`Reference to Post ${highlight.text}`}
        >
          <span>{highlight.icon}</span>
          <span>{highlight.text}</span>
        </span>
      )
    }
    lastEnd = highlight.end
  }

  // Remaining text
  if (lastEnd < text.length) {
    segments.push(text.slice(lastEnd))
  }

  return <>{segments}</>
}


// Markdown component with @mention highlighting and proper styling
function MarkdownContent({ content, className = '' }) {
  if (!content) return null

  // Custom components for ReactMarkdown with @mention support and styling
  const components = {
    // Handle text nodes - apply @mention highlighting
    p: ({ children }) => (
      <p className="mb-2 last:mb-0">
        {processChildren(children)}
      </p>
    ),
    // Styled headers
    h1: ({ children }) => <h1 className="text-xl font-bold mb-2 mt-3">{processChildren(children)}</h1>,
    h2: ({ children }) => <h2 className="text-lg font-bold mb-2 mt-3">{processChildren(children)}</h2>,
    h3: ({ children }) => <h3 className="text-base font-bold mb-1 mt-2">{processChildren(children)}</h3>,
    // Lists
    ul: ({ children }) => <ul className="list-disc list-inside mb-2 ml-2 space-y-1">{children}</ul>,
    ol: ({ children }) => <ol className="list-decimal list-inside mb-2 ml-2 space-y-1">{children}</ol>,
    li: ({ children }) => <li className="text-sm">{processChildren(children)}</li>,
    // Code blocks
    code: ({ inline, className: codeClassName, children }) => {
      if (inline) {
        return (
          <code className="px-1.5 py-0.5 bg-gray-100 text-pink-600 rounded text-sm font-mono">
            {children}
          </code>
        )
      }
      return (
        <pre className="bg-gray-900 text-gray-100 rounded-lg p-3 overflow-x-auto mb-2">
          <code className={`${codeClassName || ''} text-sm font-mono`}>{children}</code>
        </pre>
      )
    },
    // Blockquotes
    blockquote: ({ children }) => (
      <blockquote className="border-l-4 border-blue-300 pl-3 py-1 my-2 text-gray-600 italic bg-blue-50 rounded-r">
        {children}
      </blockquote>
    ),
    // Bold and italic
    strong: ({ children }) => <strong className="font-semibold">{processChildren(children)}</strong>,
    em: ({ children }) => <em className="italic">{processChildren(children)}</em>,
    // Links
    a: ({ href, children }) => (
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
        {children}
      </a>
    ),
    // Horizontal rule
    hr: () => <hr className="my-3 border-gray-200" />,
    // Tables
    table: ({ children }) => (
      <div className="overflow-x-auto mb-2">
        <table className="min-w-full border border-gray-200 rounded">{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead className="bg-gray-50">{children}</thead>,
    th: ({ children }) => <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600 border-b">{children}</th>,
    td: ({ children }) => <td className="px-3 py-2 text-sm border-b border-gray-100">{children}</td>,
  }

  // Process children to apply @mention highlighting to text nodes
  function processChildren(children) {
    if (!children) return children
    if (typeof children === 'string') {
      return renderTextWithMentions(children)
    }
    if (Array.isArray(children)) {
      return children.map((child, i) => {
        if (typeof child === 'string') {
          return <span key={i}>{renderTextWithMentions(child)}</span>
        }
        return child
      })
    }
    return children
  }

  return (
    <div className={`markdown-content ${className}`}>
      <ReactMarkdown components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}

// Tag configuration (no emojis, text only)
const TAG_CONFIG = {
  hypothesis: { label: 'Hypothesis', color: 'bg-purple-100 text-purple-700 border-purple-200' },
  evidence: { label: 'Evidence', color: 'bg-blue-100 text-blue-700 border-blue-200' },
  question: { label: 'Question', color: 'bg-orange-100 text-orange-700 border-orange-200' },
  todo: { label: 'To-Do', color: 'bg-green-100 text-green-700 border-green-200' },
  insight: { label: 'Insight', color: 'bg-yellow-100 text-yellow-700 border-yellow-200' }
}

// Separate component for Reply to use hooks properly
function ReplyItem({ reply, postId, getAuthorIcon, getAuthorColor }) {
  const [replyTags, setReplyTags] = useState(reply.tags || [])
  const [showTagMenu, setShowTagMenu] = useState(false)

  // Sync replyTags with reply.tags when it changes (e.g., from WebSocket update)
  useEffect(() => {
    setReplyTags(reply.tags || [])
  }, [reply.tags])

  const toggleTag = async (tag) => {
    const hasTag = replyTags.includes(tag)
    const newTags = hasTag ? replyTags.filter(t => t !== tag) : [...replyTags, tag]

    setReplyTags(newTags)
    setShowTagMenu(false)

    try {
      await axios.patch(`/api/post/${postId}/reply/${reply.id}/tags?client_id=${encodeURIComponent(getClientId())}`, {
        action: hasTag ? 'remove' : 'add',
        tags: [tag]
      })
    } catch (err) {
      console.error('Failed to update reply tags:', err)
      setReplyTags(replyTags)
    }
  }

  return (
    <div className="flex space-x-3 pl-4">
      <div className="text-xl">{getAuthorIcon(reply.author_type, reply.author_role)}</div>
      <div className="flex-1">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <span className={`font-semibold text-sm ${getAuthorColor(reply.author_type)}`}>
              {reply.author}
            </span>
            <span className="text-xs text-gray-400">
              {new Date(reply.created_at).toLocaleString('en-US')}
            </span>
          </div>

          {/* Reply Tags */}
          <div className="relative flex items-center gap-1">
            {replyTags.map(tag => {
              const config = TAG_CONFIG[tag]
              if (!config) return null
              return (
                <span
                  key={tag}
                  className={`px-2 py-0.5 rounded-full text-xs border ${config.color} cursor-pointer hover:opacity-80`}
                  onClick={() => toggleTag(tag)}
                  title={`Click to remove ${config.label}`}
                >
                  {config.label}
                </span>
              )
            })}

            <button
              onClick={() => setShowTagMenu(!showTagMenu)}
              className="p-0.5 rounded-full hover:bg-gray-100 text-gray-400 hover:text-gray-600"
              title="Add tag"
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" />
              </svg>
            </button>

            {showTagMenu && (
              <div className="absolute right-0 top-full mt-1 bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-10 min-w-[120px]">
                {Object.entries(TAG_CONFIG).map(([tag, config]) => (
                  <button
                    key={tag}
                    onClick={() => toggleTag(tag)}
                    className={`w-full px-2 py-1 text-left text-xs flex items-center gap-1 hover:bg-gray-50 ${replyTags.includes(tag) ? 'bg-gray-50' : ''}`}
                  >
                    <span>{config.label}</span>
                    {replyTags.includes(tag) && <span className="ml-auto text-green-500">✓</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="text-sm text-gray-700 mt-1">
          <MarkdownContent content={reply.content} />
        </div>

        {reply.visualization && (
          <div className="mt-3 p-3 bg-gray-50 rounded-lg border border-gray-200">
            <Vega spec={reply.visualization} actions={false} />
          </div>
        )}
      </div>
    </div>
  )
}

function PostCard({ post, onProactivePostCreated, typingAgent, clientId: propClientId, allPosts = [] }) {
  // Use prop clientId if provided, otherwise get from utility
  const clientId = propClientId || getClientId()

  // Available posts for #reference (exclude current post)
  const availablePosts = allPosts.filter(p => p.id !== post.id)

  // Auto-show replies if post already has replies (e.g., from auto-collaboration)
  const hasInitialReplies = (post.replies || []).length > 0
  const [showReplies, setShowReplies] = useState(hasInitialReplies)
  const [replyContent, setReplyContent] = useState('')
  const [replies, setReplies] = useState(post.replies || [])
  const [submitting, setSubmitting] = useState(false)
  const [agentReplyThinking, setAgentReplyThinking] = useState(false) // Track agent reply thinking from frontend actions
  const [isSaved, setIsSaved] = useState(post.is_saved || false) // Track saved status
  const [isLiked, setIsLiked] = useState(post.is_liked || false) // Track liked status
  const [likeRecommendations, setLikeRecommendations] = useState(post.like_recommendations || []) // Like-based recommendations

  // @Mention state for reply input
  const [showMentions, setShowMentions] = useState(false)
  const [mentionFilter, setMentionFilter] = useState('')
  const [mentionSelectedIndex, setMentionSelectedIndex] = useState(0)
  const replyInputRef = useRef(null)

  // #Post reference state for reply input
  const [showPostRefs, setShowPostRefs] = useState(false)
  const [postRefFilter, setPostRefFilter] = useState('')
  const [postRefSelectedIndex, setPostRefSelectedIndex] = useState(0)

  // Dynamic agent list for @mention (fetched from API)
  const [availableAgents, setAvailableAgents] = useState([])

  // Fetch agents for @mention autocomplete
  useEffect(() => {
    const fetchAgents = async () => {
      try {
        const response = await axios.get(`/api/agents?client_id=${encodeURIComponent(clientId)}`)
        setAvailableAgents(response.data.agents || [])
      } catch (err) {
        console.error('Failed to fetch agents for mentions:', err)
        // Fallback to empty list - will still work with role keywords in backend
      }
    }
    fetchAgents()
  }, [clientId])

  // Build mention list from agents (using first word of name as keyword)
  const AVAILABLE_MENTIONS = availableAgents.map(agent => ({
    keyword: `@${agent.name.split(' ')[0]}`,  // e.g., "@Statistical", "@James"
    display: agent.name,
    icon: agent.icon,
    role: agent.role,
    isDefault: agent.is_default
  }))

  // Tag state
  const [postTags, setPostTags] = useState(post.tags || [])
  const [showTagMenu, setShowTagMenu] = useState(false)

  // Use WebSocket-based typing indicator if available, else use local state
  const isAgentTyping = typingAgent || agentReplyThinking

  // Sync postTags with post.tags when it changes
  useEffect(() => {
    setPostTags(post.tags || [])
  }, [post.tags])

  // NOTE: We do NOT sync isSaved/isLiked from props to state here!
  // This would conflict with optimistic updates in handleSave/handleLike
  // The local state is authoritative - API response updates it directly

  // Toggle a tag on/off for this post
  const togglePostTag = async (tag) => {
    const hasTag = postTags.includes(tag)
    const newTags = hasTag
      ? postTags.filter(t => t !== tag)
      : [...postTags, tag]

    setPostTags(newTags)
    setShowTagMenu(false)

    try {
      await axios.patch(`/api/post/${post.id}/tags?client_id=${encodeURIComponent(clientId)}`, {
        action: hasTag ? 'remove' : 'add',
        tags: [tag]
      })
    } catch (err) {
      console.error('Failed to update tags:', err)
      // Revert on error
      setPostTags(postTags)
    }
  }

  //  Sync replies with post.replies when it changes (for polling updates)
  useEffect(() => {
    const newReplies = post.replies || []
    setReplies(newReplies)

    // Auto-open replies section if new replies appear
    if (newReplies.length > 0) {
      setShowReplies(true)
    }
  }, [post.replies])

  //  Auto-open replies section when agent starts typing (WebSocket)
  useEffect(() => {
    if (typingAgent) {
      setShowReplies(true)
    }
  }, [typingAgent])

  // Handle reply content change with @mention and #post detection
  const handleReplyContentChange = (e) => {
    const newContent = e.target.value
    setReplyContent(newContent)

    const cursorPos = e.target.selectionStart
    const textBeforeCursor = newContent.substring(0, cursorPos)

    // Check if we're typing @mention
    const atMatch = textBeforeCursor.match(/@(\w*)$/)
    // Check if we're typing #post reference
    const hashMatch = textBeforeCursor.match(/#(\d*)$/)

    if (atMatch) {
      setShowMentions(true)
      setMentionFilter(atMatch[1].toLowerCase())
      setMentionSelectedIndex(0)
      // Hide post refs when typing @mention
      setShowPostRefs(false)
    } else if (hashMatch) {
      setShowPostRefs(true)
      setPostRefFilter(hashMatch[1])
      setPostRefSelectedIndex(0)
      // Hide mentions when typing #post
      setShowMentions(false)
    } else {
      setShowMentions(false)
      setMentionFilter('')
      setMentionSelectedIndex(0)
      setShowPostRefs(false)
      setPostRefFilter('')
      setPostRefSelectedIndex(0)
    }
  }

  // Filter mentions for autocomplete (must be before handleMentionKeyDown)
  const filteredMentions = AVAILABLE_MENTIONS.filter(m =>
    m.keyword.toLowerCase().includes(mentionFilter) ||
    m.display.toLowerCase().includes(mentionFilter)
  )

  // Filter posts for #reference autocomplete
  const filteredPostRefs = availablePosts.filter(p => {
    const idStr = String(p.id)
    return idStr.startsWith(postRefFilter) || postRefFilter === ''
  }).slice(0, 8) // Limit to 8 results

  // Insert selected mention into reply input
  const insertMention = (mention) => {
    const input = replyInputRef.current
    if (!input) return

    const cursorPos = input.selectionStart
    const textBeforeCursor = replyContent.substring(0, cursorPos)
    const textAfterCursor = replyContent.substring(cursorPos)

    const atPos = textBeforeCursor.lastIndexOf('@')
    if (atPos === -1) return

    const newContent = textBeforeCursor.substring(0, atPos) + mention.keyword + ' ' + textAfterCursor
    setReplyContent(newContent)
    setShowMentions(false)
    setMentionFilter('')
    setMentionSelectedIndex(0)

    setTimeout(() => {
      input.focus()
      const newPos = atPos + mention.keyword.length + 1
      input.setSelectionRange(newPos, newPos)
    }, 0)
  }

  // Handle keyboard navigation for @mention dropdown
  const handleMentionKeyDown = (e) => {
    if (!showMentions || filteredMentions.length === 0) return

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setMentionSelectedIndex(prev =>
          prev < filteredMentions.length - 1 ? prev + 1 : 0
        )
        break
      case 'ArrowUp':
        e.preventDefault()
        setMentionSelectedIndex(prev =>
          prev > 0 ? prev - 1 : filteredMentions.length - 1
        )
        break
      case 'Enter':
        e.preventDefault()
        insertMention(filteredMentions[mentionSelectedIndex])
        break
      case 'Escape':
        e.preventDefault()
        setShowMentions(false)
        setMentionSelectedIndex(0)
        break
      default:
        break
    }
  }

  // Insert selected post reference into reply input
  const insertPostRef = (post) => {
    const input = replyInputRef.current
    if (!input) return

    const cursorPos = input.selectionStart
    const textBeforeCursor = replyContent.substring(0, cursorPos)
    const textAfterCursor = replyContent.substring(cursorPos)

    const hashPos = textBeforeCursor.lastIndexOf('#')
    if (hashPos === -1) return

    const newContent = textBeforeCursor.substring(0, hashPos) + `#${post.id} ` + textAfterCursor
    setReplyContent(newContent)
    setShowPostRefs(false)
    setPostRefFilter('')
    setPostRefSelectedIndex(0)

    setTimeout(() => {
      input.focus()
      const newPos = hashPos + String(post.id).length + 2
      input.setSelectionRange(newPos, newPos)
    }, 0)
  }

  // Handle keyboard navigation for #post reference dropdown
  const handlePostRefKeyDown = (e) => {
    if (!showPostRefs || filteredPostRefs.length === 0) return

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setPostRefSelectedIndex(prev =>
          prev < filteredPostRefs.length - 1 ? prev + 1 : 0
        )
        break
      case 'ArrowUp':
        e.preventDefault()
        setPostRefSelectedIndex(prev =>
          prev > 0 ? prev - 1 : filteredPostRefs.length - 1
        )
        break
      case 'Enter':
        e.preventDefault()
        insertPostRef(filteredPostRefs[postRefSelectedIndex])
        break
      case 'Escape':
        e.preventDefault()
        setShowPostRefs(false)
        setPostRefSelectedIndex(0)
        break
      default:
        break
    }
  }

  // Handle save toggle
  const handleSave = async () => {
    const wasSaved = isSaved
    setIsSaved(!wasSaved)

    try {
      const response = await axios.patch(`/api/post/${post.id}/save?client_id=${encodeURIComponent(clientId)}`)
      setIsSaved(response.data.is_saved)
    } catch (error) {
      setIsSaved(wasSaved)
      console.error('Failed to toggle save:', error)
    }
  }

  const handleLike = async () => {
    const wasLiked = isLiked
    setIsLiked(!wasLiked)

    try {
      const response = await axios.patch(`/api/post/${post.id}/like?client_id=${encodeURIComponent(clientId)}`)
      setIsLiked(response.data.is_liked)
      setLikeRecommendations(response.data.recommendations || [])
    } catch (error) {
      setIsLiked(wasLiked)
      console.error('Failed to toggle like:', error)
    }
  }

  // Handle recommendation click - add as reply
  const handleRecommendationClick = async (recommendation) => {
    try {
      // Use FormData for reply API
      const formData = new FormData()
      formData.append('client_id', clientId)
      formData.append('post_id', post.id)
      formData.append('author', 'User')
      formData.append('author_type', 'user')
      formData.append('content', recommendation)

      const response = await axios.post('/api/reply', formData)

      // Add the reply to the list (API returns {reply: {...}})
      const newReply = response.data?.reply || response.data
      if (newReply && newReply.id) {
        setReplies(prev => [...prev, newReply])
        setShowReplies(true)
      }

      // Clear recommendations after clicking
      setLikeRecommendations([])
    } catch (error) {
      console.error('Failed to add recommendation as reply:', error)
    }
  }

  const handleReply = async (e) => {
    e.preventDefault()

    if (!replyContent.trim()) return

    const currentContent = replyContent
    setReplyContent('') // Clear input immediately for better UX

    // OPTIMISTIC UPDATE: Show user reply immediately
    const optimisticReply = {
      id: Date.now(), // Temporary ID
      post_id: post.id,
      author: 'User1',
      author_type: 'user',
      author_role: null,
      content: currentContent,
      created_at: new Date().toISOString(),
      likes: 0,
      visualization: null
    }
    setReplies(prevReplies => [...prevReplies, optimisticReply])
    setSubmitting(true)

    try {
      // 1. Create User Reply in Backend
      const userFormData = new FormData()
      userFormData.append('client_id', clientId)  // Required for client isolation
      userFormData.append('post_id', post.id)
      userFormData.append('content', currentContent)
      userFormData.append('author', 'User1')
      userFormData.append('author_type', 'user')

      const userReplyResponse = await axios.post('/api/reply', userFormData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      })

      // Replace optimistic reply with real data
      const userReplyData = userReplyResponse.data.reply || userReplyResponse.data
      setReplies(prevReplies =>
        prevReplies.map(r => r.id === optimisticReply.id ? userReplyData : r)
      )

      // Stop user submission state
      setSubmitting(false)

      // Agent analysis is now automatically triggered by backend via Discussion
      // Replies come through WebSocket (new_reply events)
      // Typing indicators come through WebSocket (agent_typing events)

    } catch (error) {
      console.error('Failed to create reply:', error)
      // Remove optimistic reply on error
      setReplies(prevReplies => prevReplies.filter(r => r.id !== optimisticReply.id))
      alert('Failed to submit reply.')
      setSubmitting(false)
    }
  }

  const getAuthorIcon = (authorType, authorRole) => {
    if (authorType === 'agent') {
      // Different icons for different agent roles
      switch (authorRole) {
        case 'statistics':
          return '📊' // Statistical Analyst
        case 'visualization':
          return '🎨' // Visualization Expert
        case 'insight':
          return '💡' // Intelligence
        case 'summary':
          return '📝' // Summary Agent
        case 'scanner':
          return '🔍' // Proactive Scout
        default:
          return '🤖' // Default agent
      }
    }
    return '👤' // User
  }

  const getAuthorColor = (authorType) => {
    return authorType === 'agent' ? 'text-blue-600' : 'text-green-600'
  }

  return (
    <div id={`post-${post.id}`} className="bg-white rounded-lg shadow border border-gray-200 p-6">
      {/* Reference to previous discussion (if exists) */}
      {post.references_post_id && (
        <div className="mb-3 flex items-center space-x-2 text-xs text-gray-500 pb-3 border-b border-gray-100">
          <span>💬 Following up on discussion:</span>
          <a
            href={`#post-${post.references_post_id}`}
            className="text-blue-500 hover:underline font-medium"
            onClick={(e) => {
              e.preventDefault()
              document.getElementById(`post-${post.references_post_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
            }}
          >
            Post #{post.references_post_id}
          </a>
        </div>
      )}

      {/* Post Header */}
      <div className="flex items-start space-x-3 mb-4">
        <div className="text-3xl">{getAuthorIcon(post.author_type, post.author_role)}</div>
        <div className="flex-1">
          <div className="flex items-center space-x-2">
            <span className={`font-semibold ${getAuthorColor(post.author_type)}`}>
              {post.author}
            </span>
            {post.author_role && (
              <span className="text-sm text-gray-500">- {post.author_role}</span>
            )}
          </div>
          <p className="text-xs text-gray-400 flex items-center gap-2">
            <span>{new Date(post.created_at).toLocaleString('en-US')}</span>
            <span className="text-gray-300">·</span>
            <span className="font-medium text-gray-500">#{post.id}</span>
          </p>
        </div>

        {/* Tags Display - Right Side */}
        <div className="relative flex items-center gap-1">
          {/* Existing Tags */}
          {postTags.map(tag => {
            const config = TAG_CONFIG[tag]
            if (!config) return null
            return (
              <span
                key={tag}
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border ${config.color} cursor-pointer hover:opacity-80`}
                onClick={() => togglePostTag(tag)}
                title={`Click to remove ${config.label}`}
              >
                {config.label}
              </span>
            )
          })}

          {/* Add Tag Button */}
          <button
            onClick={() => setShowTagMenu(!showTagMenu)}
            className="p-1 rounded-full hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors"
            title="Add tag"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" />
            </svg>
          </button>

          {/* Tag Menu Popover */}
          {showTagMenu && (
            <div className="absolute right-0 top-full mt-1 bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-10 min-w-[140px]">
              {Object.entries(TAG_CONFIG).map(([tag, config]) => (
                <button
                  key={tag}
                  onClick={() => togglePostTag(tag)}
                  className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 hover:bg-gray-50 ${postTags.includes(tag) ? 'bg-gray-50' : ''}`}
                >
                  <span>{config.label}</span>
                  {postTags.includes(tag) && <span className="ml-auto text-green-500">✓</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Post Content */}
      <div className="mb-4 text-gray-800">
        <MarkdownContent content={post.content} />
      </div>

      {/* Determine if file is an image based on is_image flag OR file_type */}
      {(() => {
        const imageTypes = ['jpg', 'jpeg', 'png', 'gif', 'webp']
        const isImage = post.file_metadata?.is_image ||
          (post.file_metadata?.file_type && imageTypes.includes(post.file_metadata.file_type.toLowerCase()))

        return (
          <>
            {/* File Attachment - Only show for USER uploads with NON-image files */}
            {post.file_metadata && post.author_type === 'user' && !isImage && (
              <div className="mb-4 p-4 bg-green-50 rounded-lg border border-green-200">
                <div className="flex items-center space-x-3">
                  <div className="text-2xl">{post.file_metadata.file_type === 'csv' ? '📊' : '📄'}</div>
                  <div className="flex-1">
                    <div className="flex items-center space-x-2">
                      <span className="text-sm font-semibold text-green-800">
                        {post.file_metadata.original_filename}
                      </span>
                      <span className="px-2 py-0.5 bg-green-200 text-green-800 rounded-full text-xs">
                        {post.file_metadata.file_type?.toUpperCase() || 'FILE'}
                      </span>
                    </div>
                    <div className="flex items-center space-x-3 mt-1 text-xs text-green-700">
                      {post.file_metadata.file_type === 'csv' && post.file_metadata.rows && (
                        <>
                          <span>📏 {post.file_metadata.rows.toLocaleString()} rows</span>
                          <span>📊 {post.file_metadata.columns} cols</span>
                        </>
                      )}
                      {post.file_metadata.file_type === 'txt' && post.file_metadata.line_count && (
                        <span>📏 {post.file_metadata.line_count.toLocaleString()} lines</span>
                      )}
                      <span>💾 {((post.file_metadata.size || 0) / 1024).toFixed(2)} KB</span>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Image Attachment - Show attached images */}
            {(isImage || post.image_preview) && (
              <div className="mb-4">
                <div className="relative rounded-lg overflow-hidden border border-gray-200 inline-block">
                  <img
                    src={post.file_metadata?.image_url || post.file_metadata?.image_base64_url || post.image_preview}
                    alt={post.file_metadata?.original_filename || 'Attached image'}
                    className="max-w-full max-h-96 object-contain"
                    onError={(e) => console.error('Image load error:', e.target.src?.substring(0, 100))}
                  />
                </div>
                {post.file_metadata?.width && (
                  <p className="text-xs text-gray-500 mt-1">
                    🖼️ {post.file_metadata.original_filename} ({post.file_metadata.width}x{post.file_metadata.height})
                  </p>
                )}
              </div>
            )}

            {/* Visualization */}
            {post.visualization && (
              <div className="mb-4 p-4 bg-gray-50 rounded-lg border border-gray-200">
                <Vega spec={post.visualization} actions={false} />
              </div>
            )}

            {/* Human-in-the-loop Actions */}
            {post.hitl_options && post.hitl_options.length > 0 && (
              <div className="mb-4 p-4 bg-blue-50 rounded-lg border border-blue-200">
                <p className="text-sm font-semibold text-blue-800 mb-2">
                  What would you like to know next?
                </p>
                <div className="flex flex-wrap gap-2">
                  {post.hitl_options.map((option, idx) => (
                    <button
                      key={idx}
                      className="px-3 py-1 bg-white hover:bg-blue-100 text-blue-700 border border-blue-300 rounded-lg text-sm transition-colors"
                    >
                      {option}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Post Actions */}
            <div className="flex items-center space-x-4 pt-4 border-t border-gray-200">
              <button
                onClick={() => setShowReplies(!showReplies)}
                className="text-gray-600 hover:text-blue-600 text-sm font-medium transition-colors"
              >
                💬 Replies {replies.length > 0 ? `(${replies.length})` : ''}
              </button>
              <button
                onClick={handleLike}
                className={`text-sm font-medium transition-colors ${isLiked ? 'text-red-500' : 'text-gray-600 hover:text-red-400'
                  }`}
              >
                {isLiked ? '❤️ Liked' : '🤍 Like'}
              </button>
              <button
                onClick={handleSave}
                className={`text-sm font-medium transition-colors ${isSaved ? 'text-yellow-600' : 'text-gray-600 hover:text-yellow-600'
                  }`}
              >
                {isSaved ? '🔖 Saved' : '🔖 Save'}
              </button>
            </div>

            {/* Like-based Recommendations */}
            {likeRecommendations.length > 0 && (
              <div className="mt-4 p-4 bg-gradient-to-r from-pink-50 to-red-50 rounded-lg border border-red-200">
                <p className="text-sm font-semibold text-red-700 mb-3">
                  💡 Since you liked this, you might want to explore:
                </p>
                <div className="flex flex-col gap-2">
                  {likeRecommendations.map((rec, idx) => (
                    <button
                      key={idx}
                      className="w-full text-left px-4 py-2.5 bg-white hover:bg-red-100 text-red-700 border border-red-300 rounded-lg text-sm transition-colors shadow-sm"
                      onClick={() => handleRecommendationClick(rec)}
                    >
                      → {rec}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Replies Section */}
            {showReplies && (
              <div className="mt-4 pt-4 border-t border-gray-200 space-y-4">
                {/* Replies List */}
                {replies.map((reply, idx) => (
                  <ReplyItem
                    key={reply.id || idx}
                    reply={reply}
                    postId={post.id}
                    getAuthorIcon={getAuthorIcon}
                    getAuthorColor={getAuthorColor}
                  />
                ))}

                {/* Agent Reply Thinking Indicator - WebSocket or Local state */}
                {isAgentTyping && (
                  <div className="flex space-x-3 pl-4 py-3 bg-gradient-to-r from-blue-50 to-indigo-50 rounded-lg border border-blue-100">
                    <div className="text-xl">💬</div>
                    <div className="flex-1">
                      <div className="flex items-center space-x-2">
                        <span className="text-sm text-blue-700 font-semibold">
                          {typingAgent || 'Data Analysis Team'} is typing...
                        </span>
                        <div className="flex space-x-1">
                          <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
                          <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
                          <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
                        </div>
                      </div>
                      <span className="text-xs text-blue-600">Preparing a response...</span>
                    </div>
                  </div>
                )}

                {/* Reply Form - Moved to bottom */}
                <form onSubmit={handleReply} className="relative flex space-x-2 pt-3 border-t border-gray-100">
                  <div className="relative flex-1">
                    <input
                      ref={replyInputRef}
                      type="text"
                      value={replyContent}
                      onChange={handleReplyContentChange}
                      onKeyDown={(e) => {
                        handleMentionKeyDown(e)
                        handlePostRefKeyDown(e)
                      }}
                      placeholder="Type @ for agents, # for posts..."
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
                      disabled={submitting}
                    />

                    {/* @Mention Autocomplete Dropdown */}
                    {showMentions && filteredMentions.length > 0 && (
                      <div className="absolute left-0 bottom-full mb-1 bg-white rounded-xl shadow-xl border border-gray-200 py-1 z-20 min-w-[240px] overflow-hidden">
                        <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-100 bg-gray-50 flex items-center gap-2">
                          <span>🤖</span>
                          <span>Mention an Agent</span>
                          <span className="ml-auto text-[10px] text-gray-400">↑↓ Enter</span>
                        </div>
                        {filteredMentions.map((mention, index) => (
                          <button
                            key={mention.keyword}
                            type="button"
                            onClick={() => insertMention(mention)}
                            className={`w-full px-3 py-2.5 text-left flex items-center gap-3 transition-all ${index === mentionSelectedIndex
                              ? 'bg-gradient-to-r from-blue-50 to-indigo-50 border-l-2 border-blue-500'
                              : 'hover:bg-gray-50 border-l-2 border-transparent'
                              }`}
                          >
                            <span className={`text-xl ${index === mentionSelectedIndex ? 'scale-110' : ''} transition-transform`}>
                              {mention.icon}
                            </span>
                            <div className="flex flex-col">
                              <span className={`font-semibold ${index === mentionSelectedIndex ? 'text-blue-700' : 'text-gray-800'}`}>
                                {mention.keyword}
                              </span>
                              <span className="text-xs text-gray-500">{mention.display}</span>
                            </div>
                            {index === mentionSelectedIndex && (
                              <span className="ml-auto text-blue-500 text-xs">⏎</span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}

                    {/* #Post Reference Autocomplete Dropdown */}
                    {showPostRefs && filteredPostRefs.length > 0 && (
                      <div className="absolute left-0 bottom-full mb-1 bg-white rounded-xl shadow-xl border border-gray-200 py-1 z-20 min-w-[280px] max-h-[300px] overflow-y-auto">
                        <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-100 bg-gray-50 flex items-center gap-2 sticky top-0">
                          <span>📎</span>
                          <span>Reference a Post</span>
                          <span className="ml-auto text-[10px] text-gray-400">↑↓ Enter</span>
                        </div>
                        {filteredPostRefs.map((refPost, index) => (
                          <button
                            key={refPost.id}
                            type="button"
                            onClick={() => insertPostRef(refPost)}
                            className={`w-full px-3 py-2.5 text-left flex items-start gap-3 transition-all ${index === postRefSelectedIndex
                              ? 'bg-gradient-to-r from-violet-50 to-purple-50 border-l-2 border-violet-500'
                              : 'hover:bg-gray-50 border-l-2 border-transparent'
                              }`}
                          >
                            <span className={`text-sm font-bold ${index === postRefSelectedIndex ? 'text-violet-600' : 'text-gray-500'}`}>
                              #{refPost.id}
                            </span>
                            <div className="flex flex-col flex-1 min-w-0">
                              <span className={`text-sm truncate ${index === postRefSelectedIndex ? 'text-violet-700 font-medium' : 'text-gray-700'}`}>
                                {refPost.content?.substring(0, 50)}...
                              </span>
                              <span className="text-xs text-gray-400">
                                {refPost.author} • {(refPost.replies || []).length} replies
                              </span>
                            </div>
                            {index === postRefSelectedIndex && (
                              <span className="text-violet-500 text-xs">⏎</span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  <button
                    type="submit"
                    disabled={submitting || !replyContent.trim()}
                    className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors disabled:bg-gray-300"
                  >
                    {submitting ? '...' : 'Reply'}
                  </button>
                </form>
              </div>
            )}
          </>
        )
      })()}
    </div>
  )
}

export default PostCard
