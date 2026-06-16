import { useState, useEffect, useCallback, useMemo } from 'react'
import FeedView from './FeedView'
import ChatbotView from './ChatbotView'
import DataView from './DataView'
import AgentsView from './AgentsView'
import InsightTimeline from './InsightTimeline'
import BranchView from './BranchView'
import SettingsPanel from './SettingsPanel'
import AgentManagementModal from './AgentManagementModal'
import AgentStatusRow from './AgentStatusRow'
import SearchBar from './SearchBar'
import axios from 'axios'
import { useWebSocket } from '../hooks/useWebSocket'
import { getClientId } from '../utils/clientId'

function MainFeed({ communicationMode, activeView, onViewChange, activeNodeId, onNodeActive }) {
  const [posts, setPosts] = useState([])
  const [loading, setLoading] = useState(false)
  const [lastFileId, setLastFileId] = useState(null) // Track last uploaded file ID
  const [nextSteps, setNextSteps] = useState([]) // Next steps for carousel
  const [showAgentModal, setShowAgentModal] = useState(false)

  const postsVersion = useMemo(() => {
    let replyCount = 0
    let latestTimestamp = ''

    posts.forEach(post => {
      if (post.created_at && post.created_at > latestTimestamp) {
        latestTimestamp = post.created_at
      }
      const replies = post.replies || []
      replies.forEach(reply => {
        replyCount += 1
        if (reply.created_at && reply.created_at > latestTimestamp) {
          latestTimestamp = reply.created_at
        }
      })
    })

    return `${posts.length}:${replyCount}:${latestTimestamp}`
  }, [posts])

  // Get client ID for this tab (ensures isolation)
  const clientId = getClientId()

  // WebSocket connection (now includes clientId)
  const { isConnected, lastMessage, typingAgents, typingNewPost } = useWebSocket()

  // Fetch posts on mount and when view changes
  useEffect(() => {
    if (activeView === 'feed') {
      fetchPosts()

      // Fallback polling (less frequent now that we have WebSocket)
      const pollInterval = setInterval(() => {
        fetchPosts(true) // Silent refresh
      }, 15000) // Poll every 15 seconds as backup

      return () => clearInterval(pollInterval)
    }
  }, [activeView])

  // Fetch next steps when lastFileId changes
  useEffect(() => {
    if (lastFileId) {
      fetchNextSteps()
    }
  }, [lastFileId])

  // Poll for next steps
  useEffect(() => {
    if (!lastFileId) return

    const pollInterval = setInterval(() => {
      fetchNextSteps(true) // Silent fetch
    }, 15000) // Check every 15 seconds

    return () => clearInterval(pollInterval)
  }, [lastFileId])

  const fetchNextSteps = async (silent = false) => {
    if (!lastFileId) return

    try {
      // Try new next-steps API first (extracts from proactive posts)
      const response = await axios.get(`/api/next-steps/${lastFileId}`, {
        params: { client_id: clientId }
      })
      const steps = response.data.next_steps || []

      if (steps.length > 0) {
        setNextSteps(steps)
        if (!silent) {
        }
        return
      }

      // Fallback: try summary API
      try {
        const summaryResponse = await axios.get(`/api/analysis/summary/${lastFileId}`, {
          params: { client_id: clientId }
        })
        const summarySteps = summaryResponse.data.next_steps || []
        setNextSteps(summarySteps)
        if (!silent && summarySteps.length > 0) {
        }
      } catch (summaryErr) {
        // 404 is normal - no summary yet
        if (summaryErr.response?.status !== 404) {
          console.error('Failed to fetch next steps from summary:', summaryErr)
        }
      }
    } catch (err) {
      console.error('Failed to fetch next steps:', err)
    }
  }

  //  NEW: API fallback - fetch complete post data when needed
  const fetchPostData = useCallback(async (postId) => {
    try {
      const response = await axios.get(`/api/post/${postId}`, {
        params: { client_id: clientId }
      })
      return response.data.post
    } catch (error) {
      console.error(`Failed to fetch post ${postId}:`, error)
      return null
    }
  }, [clientId])

  // Handle WebSocket messages
  useEffect(() => {
    if (!lastMessage) return

    if (lastMessage.type === 'new_reply') {
      // Handle new reply: update the specific post's replies
      const { post_id, reply } = lastMessage

      //  NEW: Check if reply has visualization - if from agent, might need API fallback
      const hasVisualization = reply.visualization !== null && reply.visualization !== undefined
      const isAgentReply = reply.author_type === 'agent'

      setPosts(prevPosts => {
        return prevPosts.map(post => {
          if (post.id === post_id) {
            // Check if reply already exists (avoid duplicates)
            const replyExists = post.replies?.some(r => r.id === reply.id)
            if (replyExists) {
              //  NEW: If existing reply has no visualization but new one does, update it
              if (hasVisualization) {
                return {
                  ...post,
                  replies: post.replies.map(r =>
                    r.id === reply.id ? { ...r, ...reply } : r
                  )
                }
              }
              return post
            }
            return {
              ...post,
              replies: [...(post.replies || []), reply]
            }
          }
          return post
        })
      })

      //  NEW: API fallback for agent replies that might have visualization
      // Fetch complete post data after a short delay to ensure backend has saved all data
      if (isAgentReply) {
        setTimeout(async () => {
          const completePost = await fetchPostData(post_id)
          if (completePost) {
            setPosts(prevPosts => {
              return prevPosts.map(post => {
                if (post.id === post_id) {
                  // Merge replies - keep newer/more complete versions
                  const mergedReplies = completePost.replies || []
                  return { ...post, replies: mergedReplies }
                }
                return post
              })
            })
          }
        }, 1000) // Wait 1 second for backend to finish processing
      }
    } else if (lastMessage.type === 'new_post') {
      // Handle new post
      const { post } = lastMessage
      setPosts(prevPosts => {
        const exists = prevPosts.some(p => p.id === post.id)
        if (exists) {
          //  NEW: Update existing post with newer data, preserve user interaction state
          return prevPosts.map(p => p.id === post.id ? {
            ...p,
            ...post,
            is_saved: p.is_saved,
            is_liked: p.is_liked,
            like_recommendations: p.like_recommendations
          } : p)
        }
        return [...prevPosts, post]
      })

      //  NEW: API fallback for new posts - ensure we have complete data
      if (post.author_type === 'agent') {
        setTimeout(async () => {
          const completePost = await fetchPostData(post.id)
          if (completePost) {
            setPosts(prevPosts => {
              return prevPosts.map(p => {
                if (p.id === post.id) {
                  // Preserve user interaction state
                  return {
                    ...p,
                    ...completePost,
                    is_saved: p.is_saved,
                    is_liked: p.is_liked,
                    like_recommendations: p.like_recommendations
                  }
                }
                return p
              })
            })
          }
        }, 1500) // Wait 1.5 seconds for backend processing
      }
    } else if (lastMessage.type === 'reply_tags_updated') {
      // Handle reply tags update: update tags for specific reply
      const { post_id, reply_id, tags } = lastMessage
      setPosts(prevPosts => {
        return prevPosts.map(post => {
          if (post.id === post_id) {
            return {
              ...post,
              replies: (post.replies || []).map(reply => {
                if (reply.id === reply_id) {
                  return { ...reply, tags: tags }
                }
                return reply
              })
            }
          }
          return post
        })
      })
    } else if (lastMessage.type === 'feed_updated') {
      // Generic update: refetch feed
      fetchPosts(true)
    } else if (lastMessage.type === 'post_saved') {
      // Handle save toggle
      const { post_id, is_saved } = lastMessage
      setPosts(prevPosts =>
        prevPosts.map(p => p.id === post_id ? { ...p, is_saved } : p)
      )
    } else if (lastMessage.type === 'post_liked') {
      // Handle like toggle
      const { post_id, is_liked, recommendations } = lastMessage
      setPosts(prevPosts =>
        prevPosts.map(p => p.id === post_id ? { ...p, is_liked, like_recommendations: recommendations || [] } : p)
      )
    }
  }, [lastMessage])

  const fetchPosts = async (silent = false) => {
    try {
      if (!silent) setLoading(true)
      const response = await axios.get('/api/feed', {
        params: { client_id: clientId }
      })
      const fetchedPosts = response.data.posts || []
      setPosts(fetchedPosts)

      // Auto-detect lastFileId from posts (skip image files - they're not analysis data)
      const imageTypes = ['jpg', 'jpeg', 'png', 'gif', 'webp']
      if (!lastFileId && fetchedPosts.length > 0) {
        for (let i = fetchedPosts.length - 1; i >= 0; i--) {
          const post = fetchedPosts[i]
          const fileType = post.file_metadata?.file_type?.toLowerCase()
          // Only track data files (CSV, TXT), not images
          if (post.file_metadata?.file_id && !imageTypes.includes(fileType)) {
            setLastFileId(post.file_metadata.file_id)
            break
          }
        }
      }
    } catch (error) {
      console.error('Failed to fetch posts:', error)
    } finally {
      if (!silent) setLoading(false)
    }
  }

  const handlePostCreated = useCallback((newPost) => {
    // Use functional update to ensure we're working with the latest state
    setPosts(prevPosts => {
      // Check if post with this ID already exists (optimistic update)
      const existingIndex = prevPosts.findIndex(p => p.id === newPost.id)

      if (existingIndex !== -1) {
        // Update existing post (replace optimistic with real data)
        const updatedPosts = [...prevPosts]
        updatedPosts[existingIndex] = newPost
        return updatedPosts
      } else {
        // Add new post
        return [...prevPosts, newPost]
      }
    })

    // Track file_id for continuous conversation (skip image files)
    const imageTypes = ['jpg', 'jpeg', 'png', 'gif', 'webp']
    const fileType = newPost.file_metadata?.file_type?.toLowerCase()
    if (newPost.file_metadata?.file_id && !imageTypes.includes(fileType)) {
      setLastFileId(newPost.file_metadata.file_id)
    }
  }, [])

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {activeView === 'data' ? (
        // Data View
        <DataView />
      ) : activeView === 'agents' ? (
        // Agents View
        <AgentsView />
      ) : activeView === 'timeline' ? (
        // Insight Timeline View
        <InsightTimeline
          onViewChange={onViewChange}
          onNavigateToPost={(postId, replyId) => {
            onViewChange('feed')
            // Small delay to allow view to render, then scroll
            setTimeout(() => {
              const targetId = replyId ? `reply-${postId}-${replyId}` : `post-${postId}`
              const element = document.getElementById(targetId)
              if (element) {
                element.scrollIntoView({ behavior: 'smooth', block: 'center' })
                element.classList.add('ring-2', 'ring-blue-400', 'ring-offset-2')
                setTimeout(() => {
                  element.classList.remove('ring-2', 'ring-blue-400', 'ring-offset-2')
                }, 2000)
              }
            }, 300)
          }}
        />
      ) : activeView === 'branch' ? (
        // Branch View (Timeline)
        <BranchView
          clientId={clientId}
          wsMessage={lastMessage}
          postsVersion={postsVersion}
          activeNodeId={activeNodeId}
          onNodeActive={onNodeActive}
          onNavigateToPost={(postId) => {
            onViewChange('feed')
            setTimeout(() => {
              const element = document.getElementById(`post-${postId}`)
              if (element) {
                element.scrollIntoView({ behavior: 'smooth', block: 'center' })
                element.classList.add('ring-2', 'ring-blue-400', 'ring-offset-2')
                setTimeout(() => {
                  element.classList.remove('ring-2', 'ring-blue-400', 'ring-offset-2')
                }, 2000)
              }
            }, 300)
          }}
        />
      ) : activeView === 'settings' ? (
        // Settings Panel (inline)
        <SettingsPanel clientId={clientId} />
      ) : (
        // Feed View
        <>
          {/* Header */}
          <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-xl font-semibold text-gray-800">
                  {communicationMode === 'sns' ? 'Feed' : 'Chat'}
                </h2>
                <p className="text-sm text-gray-500">
                  Real-time agent collaboration
                </p>
              </div>

              {/* Right side controls */}
              <div className="flex items-center gap-4">
                {/* Search Bar */}
                <SearchBar
                  clientId={clientId}
                  onResultClick={(result) => {
                    // Scroll to the post
                    const postElement = document.getElementById(`post-${result.post_id}`)
                    if (postElement) {
                      postElement.scrollIntoView({ behavior: 'smooth', block: 'center' })
                      // Highlight effect
                      postElement.classList.add('ring-2', 'ring-blue-400', 'ring-offset-2')
                      setTimeout(() => {
                        postElement.classList.remove('ring-2', 'ring-blue-400', 'ring-offset-2')
                      }, 2000)
                    }
                  }}
                />

                {/* Divider */}
                <div className="h-8 w-px bg-gray-200" />

                {/* Agent Status Row */}
                <AgentStatusRow
                  typingAgents={typingAgents}
                  typingNewPost={typingNewPost}
                />

                {/* Divider */}
                <div className="h-8 w-px bg-gray-200" />

                {/* Agent Management Button */}
                <button
                  onClick={() => setShowAgentModal(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-600 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
                  title="Manage Agents"
                >
                  <span>⚙️</span>
                  <span>Settings</span>
                </button>
              </div>
            </div>
          </div>

          {/* Agent Management Modal */}
          <AgentManagementModal
            isOpen={showAgentModal}
            onClose={() => setShowAgentModal(false)}
            clientId={clientId}
          />

          {/* Render based on mode */}
          {communicationMode === 'sns' ? (
            <FeedView
              posts={posts}
              loading={loading}
              onPostCreated={handlePostCreated}
              lastFileId={lastFileId}
              typingAgents={typingAgents}
              typingNewPost={typingNewPost}
              nextSteps={nextSteps}
              clientId={clientId}
            />
          ) : (
            <ChatbotView
              posts={posts}
              onPostCreated={handlePostCreated}
              lastFileId={lastFileId}
              typingAgents={typingAgents}
              typingNewPost={typingNewPost}
              clientId={clientId}
            />
          )}
        </>
      )}
    </div>
  )
}

export default MainFeed
