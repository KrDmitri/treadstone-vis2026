import { useState, useEffect, useRef } from 'react'
import ChatMessage from './ChatMessage'
import ChatInput from './ChatInput'

function ChatbotView({ posts, onPostCreated, lastFileId, clientId }) {
  const messagesEndRef = useRef(null)
  const [isAgentThinking, setIsAgentThinking] = useState(false)

  // Convert posts and replies to chronologically sorted messages
  const getMessages = () => {
    const messages = []
    
    posts.forEach(post => {
      messages.push({
        id: `post-${post.id}`,
        type: 'post',
        author: post.author,
        author_type: post.author_type,
        author_role: post.author_role,
        content: post.content,
        visualization: post.visualization,
        hitl_options: post.hitl_options,
        file_metadata: post.file_metadata,
        created_at: post.created_at,
      })
      
      if (post.replies && post.replies.length > 0) {
        post.replies.forEach(reply => {
          messages.push({
            id: `reply-${reply.id}`,
            type: 'reply',
            author: reply.author,
            author_type: reply.author_type,
            content: reply.content,
            created_at: reply.created_at,
          })
        })
      }
    })
    
    // Sort by time
    return messages.sort((a, b) => 
      new Date(a.created_at) - new Date(b.created_at)
    )
  }

  const messages = getMessages()

  // Auto-scroll when new messages are added
  useEffect(() => {
    // Check if the last message has visualization (needs more time to render)
    const lastMessage = messages[messages.length - 1]
    const hasVisualization = lastMessage?.visualization
    
    // Use longer delay for messages with visualizations
    const delay = hasVisualization ? 300 : 100
    
    const scrollTimer = setTimeout(() => {
      scrollToBottom()
    }, delay)
    
    return () => clearTimeout(scrollTimer)
  }, [messages.length, isAgentThinking]) // Also trigger when agent thinking state changes

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }

  return (
    <div className="flex flex-col h-full">
      {/* Chat Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
        <div className="flex items-center space-x-3">
          <div className="text-2xl">💬</div>
          <div>
            <h2 className="text-xl font-semibold text-gray-800">AI Analysis Assistant</h2>
            <p className="text-sm text-gray-500">Here to help with your data analysis</p>
          </div>
        </div>
      </div>

      {/* Messages Area */}
      <div className="flex-1 overflow-y-auto p-6 space-y-4 bg-gray-50">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="text-6xl mb-4">🤖</div>
            <h3 className="text-xl font-semibold text-gray-700 mb-2">
              Start a conversation
            </h3>
            <p className="text-gray-500">
              Ask questions about data analysis<br />
              and our AI agents will assist you.
            </p>
          </div>
        ) : (
          <>
            {messages.map((message) => (
              <ChatMessage key={message.id} message={message} />
            ))}
            
            {/* Agent Thinking Indicator */}
            {isAgentThinking && (
              <div className="flex justify-start">
                <div className="max-w-3xl">
                  <div className="flex space-x-3">
                    {/* Avatar - using generic robot since we don't know which agent yet */}
                    <div className="flex-shrink-0">
                      <div className="w-10 h-10 rounded-full flex items-center justify-center text-xl bg-blue-100">
                        💭
                      </div>
                    </div>
                    
                    {/* Typing indicator */}
                    <div className="flex flex-col items-start ml-3">
                      <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-none px-4 py-3">
                        <div className="flex space-x-1">
                          <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
                          <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
                          <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
                        </div>
                      </div>
                      <span className="text-xs text-gray-400 mt-1 ml-1">Analyzing...</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
            
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Input Area */}
      <div className="bg-white border-t border-gray-200 p-4 flex-shrink-0">
        <ChatInput 
          onPostCreated={onPostCreated} 
          lastFileId={lastFileId}
          onAgentThinking={setIsAgentThinking}
          clientId={clientId}
        />
      </div>
    </div>
  )
}

export default ChatbotView

