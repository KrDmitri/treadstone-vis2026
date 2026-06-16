import { useRef, useEffect, useState, useCallback } from 'react'
import { ChevronDown } from 'lucide-react'
import PostCreator from './PostCreator'
import PostCard from './PostCard'
import NextStepsCarousel from './NextStepsCarousel'

function FeedView({ posts, loading, onPostCreated, lastFileId, typingAgents = {}, typingNewPost = null, nextSteps = [], clientId }) {
  const scrollContainerRef = useRef(null)
  const feedEndRef = useRef(null)
  const prevPostCountRef = useRef(posts.length)
  const [isNearBottom, setIsNearBottom] = useState(true)
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const [unseenPostCount, setUnseenPostCount] = useState(0)

  // Check if agent is creating a new post (from WebSocket)
  const hasPostTyping = typingNewPost !== null

  const updateScrollState = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container) return

    const threshold = 120
    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight
    const nearBottom = distanceFromBottom <= threshold

    setIsNearBottom(nearBottom)
    setShowScrollToBottom(!nearBottom)

    if (nearBottom) {
      setUnseenPostCount(0)
    }
  }, [])

  useEffect(() => {
    updateScrollState()
  }, [updateScrollState, posts.length, hasPostTyping])

  useEffect(() => {
    const previousCount = prevPostCountRef.current
    if (posts.length > previousCount && !isNearBottom) {
      setUnseenPostCount(prev => prev + (posts.length - previousCount))
      setShowScrollToBottom(true)
    }
    prevPostCountRef.current = posts.length
  }, [posts.length, isNearBottom])

  const scrollToBottom = useCallback(() => {
    feedEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    setUnseenPostCount(0)
    setShowScrollToBottom(false)
  }, [])

  const handleProactivePostCreated = (proactivePost) => {
    onPostCreated(proactivePost)
  }

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      {/* Post Creator */}
      <div
        className="bg-white border-b border-gray-200 p-6 overflow-auto flex-shrink-0"
        style={{}}
      >
        <PostCreator
          onPostCreated={onPostCreated}
          lastFileId={lastFileId}
          clientId={clientId}
        />
      </div>

      {/* Posts Feed */}
      <div
        ref={scrollContainerRef}
        className="relative flex-1 overflow-y-auto p-6 space-y-4"
        onScroll={updateScrollState}
      >
        {loading ? (
          <div className="text-center py-8 text-gray-500">Loading...</div>
        ) : posts.length === 0 ? (
          <div className="text-center py-8 text-gray-500">
            No posts yet. Create your first post!
          </div>
        ) : (
          <>
            {(() => {
              const SEGMENT_SIZE = 3
              const elements = []
              let segmentIndex = 0

              posts.forEach((post, index) => {
                elements.push(
                  <PostCard
                    key={post.id}
                    post={post}
                    onProactivePostCreated={handleProactivePostCreated}
                    typingAgent={typingAgents[post.id]?.agent}
                    clientId={clientId}
                    allPosts={posts}
                  />
                )

                const isEndOfSegment = (index + 1) % SEGMENT_SIZE === 0
                const isNotLastPost = index !== posts.length - 1

                if (isEndOfSegment && isNotLastPost && lastFileId && !hasPostTyping) {
                  const segmentStart = index - SEGMENT_SIZE + 1
                  const segmentPosts = posts.slice(segmentStart, index + 1)
                  const segmentPostIds = segmentPosts.map(p => p.id)

                  elements.push(
                    <NextStepsCarousel
                      key={`inline-${segmentIndex}`}
                      posts={segmentPosts}
                      onPostCreated={onPostCreated}
                      fileId={lastFileId}
                      variant="inline"
                      segmentPostIds={segmentPostIds}
                      segmentIndex={segmentIndex}
                      clientId={clientId}
                    />
                  )
                  segmentIndex++
                }
              })

              return elements
            })()}

            {/* Bottom Next Steps Carousel */}
            {lastFileId && !hasPostTyping && (
              <NextStepsCarousel
                posts={posts}
                onPostCreated={onPostCreated}
                fileId={lastFileId}
                variant="bottom"
                clientId={clientId}
              />
            )}

            {/* Agent New Post Typing Indicator */}
            {hasPostTyping && (
              <div className="bg-gradient-to-r from-purple-50 to-pink-50 border border-purple-200 rounded-xl p-5 shadow-sm animate-pulse">
                <div className="flex items-center space-x-3">
                  <div className="flex space-x-1">
                    <div className="w-2.5 h-2.5 bg-purple-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
                    <div className="w-2.5 h-2.5 bg-purple-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
                    <div className="w-2.5 h-2.5 bg-purple-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
                  </div>
                  <div className="flex flex-col">
                    <span className="text-purple-800 font-semibold text-sm">
                      📝 {typingNewPost?.agent || 'Data Scout Agent'} is creating a new insight...
                    </span>
                    <span className="text-purple-600 text-xs">
                      Discovered something interesting in your data
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Scroll anchor */}
            <div ref={feedEndRef} />
          </>
        )}

        {showScrollToBottom && (
          <button
            onClick={scrollToBottom}
            className="sticky bottom-4 ml-auto flex items-center gap-2 rounded-full bg-gray-900/90 px-4 py-2 text-sm font-medium text-white shadow-lg backdrop-blur transition hover:bg-gray-900"
            title="Scroll to latest posts"
          >
            <ChevronDown className="h-4 w-4" />
            <span>
              {unseenPostCount > 0 ? `${unseenPostCount} new post${unseenPostCount > 1 ? 's' : ''}` : 'Latest posts'}
            </span>
          </button>
        )}
      </div>
    </div>
  )
}

export default FeedView
