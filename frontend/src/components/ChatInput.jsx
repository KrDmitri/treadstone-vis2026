import { useState } from 'react'
import axios from 'axios'
import { getClientId } from '../utils/clientId'

function ChatInput({ onPostCreated, lastFileId, onAgentThinking, clientId: propClientId }) {
  // Use prop clientId if provided, otherwise get from utility
  const clientId = propClientId || getClientId()
  const [content, setContent] = useState('')
  const [file, setFile] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    
    if (!content.trim()) {
      return
    }

    try {
      setSubmitting(true)
      
      // Notify parent that agent will start thinking
      if (onAgentThinking) onAgentThinking(true)
      
      const formData = new FormData()
      formData.append('client_id', clientId)  // Required for client isolation
      formData.append('content', content)
      formData.append('author', 'User1')
      formData.append('author_type', 'user')
      
      if (file) {
        formData.append('file', file)
      }

      const response = await axios.post('/api/post', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      })

      onPostCreated(response.data)
      
      // Trigger agent analysis if file_id available
      const fileIdToAnalyze = response.data.file_metadata?.file_id || lastFileId
      
      if (fileIdToAnalyze) {
        try {
          const analysisResponse = await axios.post('/api/agent/analyze', {
            file_id: fileIdToAnalyze,
            message: content,
            client_id: clientId  // Required for client isolation
          })

          // Create agent post with analysis
          const agentFormData = new FormData()
          agentFormData.append('client_id', clientId)  // Required for client isolation
          agentFormData.append('content', analysisResponse.data.content)
          agentFormData.append('author', analysisResponse.data.agent)
          agentFormData.append('author_type', 'agent')
          agentFormData.append('author_role', analysisResponse.data.agent_role)
          
          if (analysisResponse.data.visualization) {
            agentFormData.append('visualization', JSON.stringify(analysisResponse.data.visualization))
          } else {
            console.warn('No visualization in response')
          }
          
          if (analysisResponse.data.hitl_options) {
            agentFormData.append('hitl_options', JSON.stringify(analysisResponse.data.hitl_options))
          }
          
          const agentPost = await axios.post('/api/post', agentFormData, {
            headers: {
              'Content-Type': 'multipart/form-data',
            },
          })
          
          onPostCreated(agentPost.data)
        } catch (analysisError) {
          console.error('Agent analysis failed:', analysisError)
        } finally {
          // Agent finished thinking (success or error)
          if (onAgentThinking) onAgentThinking(false)
        }
      } else {
        // No agent analysis needed, stop thinking immediately
        if (onAgentThinking) onAgentThinking(false)
      }
      
      setContent('')
      setFile(null)
    } catch (error) {
      console.error('Failed to send message:', error)
      // Stop thinking on error
      if (onAgentThinking) onAgentThinking(false)
    } finally {
      setSubmitting(false)
    }
  }

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2">
      {file && (
        <div className="flex items-center space-x-2 px-3 py-2 bg-gray-100 rounded-lg">
          <span className="text-sm text-gray-600">📎 {file.name}</span>
          <button
            type="button"
            onClick={() => setFile(null)}
            className="text-red-500 hover:text-red-700 text-sm"
          >
            ✕
          </button>
        </div>
      )}

      <div className="flex items-end space-x-2">
        {/* File Attach Button */}
        <label className="cursor-pointer flex-shrink-0" title="Attach CSV or TXT file">
          <input
            type="file"
            accept=".csv,.txt"
            onChange={(e) => setFile(e.target.files[0])}
            className="hidden"
            disabled={submitting}
          />
          <div className="w-10 h-10 flex items-center justify-center bg-gray-100 hover:bg-gray-200 rounded-full transition-colors">
            <span className="text-xl">📎</span>
          </div>
        </label>

        {/* Text Input */}
        <div className="flex-1 relative">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Type your message... (Shift+Enter for new line)"
            className="w-full px-4 py-3 pr-12 border border-gray-300 rounded-2xl focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none"
            rows="1"
            style={{
              minHeight: '44px',
              maxHeight: '120px',
            }}
            disabled={submitting}
          />
        </div>

        {/* Send Button */}
        <button
          type="submit"
          disabled={submitting || !content.trim()}
          className="flex-shrink-0 w-10 h-10 flex items-center justify-center bg-blue-500 hover:bg-blue-600 text-white rounded-full transition-colors disabled:bg-gray-300 disabled:cursor-not-allowed"
        >
          {submitting ? (
            <span className="text-sm">⏳</span>
          ) : (
            <span className="text-xl">➤</span>
          )}
        </button>
      </div>

      <p className="text-xs text-gray-400 px-2">
        Press Enter to send · Shift+Enter for new line
      </p>
    </form>
  )
}

export default ChatInput
