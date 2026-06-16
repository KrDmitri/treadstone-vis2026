import { useState, useEffect } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import { getClientId } from '../../utils/clientId'

function SummaryTab() {
  const clientId = getClientId()
  const [summary, setSummary] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState(null)
  const [fileId, setFileId] = useState(null)

  // Get current file_id from latest post
  useEffect(() => {
    const fetchCurrentFileId = async () => {
      try {
        const response = await axios.get('/api/feed', { params: { client_id: clientId } })
        const posts = response.data.posts || []
        
        // Find the latest post with file_metadata
        for (let i = posts.length - 1; i >= 0; i--) {
          const post = posts[i]
          if (post.file_metadata && post.file_metadata.file_id) {
            setFileId(post.file_metadata.file_id)
            break
          }
        }
      } catch (err) {
        console.error('Failed to fetch file_id:', err)
      }
    }

    // Initial fetch
    fetchCurrentFileId()
    
    // Poll for file uploads every 3 seconds
    const pollInterval = setInterval(() => {
      fetchCurrentFileId()
    }, 3000)

    return () => clearInterval(pollInterval)
  }, [])

  // Auto-fetch summary when fileId changes
  useEffect(() => {
    if (fileId) {
      fetchSummary()
    }
  }, [fileId])

  // Polling for auto-generated summaries
  useEffect(() => {
    if (!fileId) return

    const pollInterval = setInterval(() => {
      fetchSummary(true) // silent fetch
    }, 10000) // Check every 10 seconds

    return () => clearInterval(pollInterval)
  }, [fileId])

  const fetchSummary = async (silent = false) => {
    if (!fileId) {
      setError('No dataset loaded yet.')
      return
    }

    if (!silent) {
      setIsLoading(true)
      setError(null)
    }

    try {
      const response = await axios.get(`/api/analysis/summary/${fileId}`, { params: { client_id: clientId } })
      setSummary(response.data)
      if (!silent) {
      }
    } catch (err) {
      if (err.response && err.response.status === 404) {
        // No summary yet - this is normal
        if (!silent) {
          setSummary(null)
        }
      } else {
        console.error('Failed to fetch summary:', err)
        if (!silent) {
          setError('Failed to load summary.')
        }
      }
    } finally {
      if (!silent) {
        setIsLoading(false)
      }
    }
  }

  const generateSummary = async () => {
    if (!fileId) {
      alert('No dataset loaded yet.')
      return
    }

    setIsLoading(true)
    setError(null)

    try {
      const response = await axios.post('/api/analysis/summary', 
        { file_id: fileId },
        { params: { client_id: clientId } }
      )

      if (response.data.success) {
        setSummary(response.data)
      } else {
        setError(response.data.error || 'Failed to generate summary')
      }
    } catch (err) {
      console.error('Failed to generate summary:', err)
      setError(err.response?.data?.detail || 'Failed to generate summary')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Header with Generate Button */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-700">Conversation Summary</h3>
          {summary && summary.content ? (
            <p className="text-xs text-gray-500 mt-1">
              Last updated: {new Date(summary.created_at || summary.updated_at).toLocaleTimeString('en-US')}
            </p>
          ) : fileId ? (
            <p className="text-xs text-blue-600 mt-1 animate-pulse">
              📊 Dataset loaded - ready to analyze
            </p>
          ) : (
            <p className="text-xs text-gray-500 mt-1">
              Upload a dataset to start
            </p>
          )}
        </div>
        {fileId && (
          <button
            onClick={generateSummary}
            disabled={isLoading}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed"
          >
            {isLoading ? '⏳ Generating...' : '🔄 Generate Summary'}
          </button>
        )}
      </div>

      {/* Error State */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-sm text-red-800">❌ {error}</p>
        </div>
      )}

      {/* Loading State */}
      {isLoading && !summary && (
        <div className="text-center py-12">
          <div className="animate-pulse space-y-3">
            <div className="text-4xl">💭</div>
            <p className="text-sm text-gray-600">Generating summary...</p>
            <p className="text-xs text-gray-500">This may take 10-20 seconds</p>
          </div>
        </div>
      )}

      {/* Summary Content */}
      {!isLoading && summary && summary.content && (
        <div className="bg-white border border-gray-200 rounded-lg p-6">
          <div className="prose prose-sm max-w-none text-gray-800">
            <ReactMarkdown
              components={{
                h1: ({node, ...props}) => <h1 className="text-2xl font-bold text-gray-900 mt-6 mb-4" {...props} />,
                h2: ({node, ...props}) => <h2 className="text-xl font-bold text-gray-900 mt-5 mb-3" {...props} />,
                h3: ({node, ...props}) => <h3 className="text-lg font-semibold text-gray-800 mt-4 mb-2" {...props} />,
                p: ({node, ...props}) => <p className="mb-3 text-gray-700" {...props} />,
                ul: ({node, ...props}) => <ul className="list-disc list-inside mb-3 space-y-1" {...props} />,
                ol: ({node, ...props}) => <ol className="list-decimal list-inside mb-3 space-y-1" {...props} />,
                li: ({node, ...props}) => <li className="text-gray-700" {...props} />,
                strong: ({node, ...props}) => <strong className="font-semibold text-gray-900" {...props} />,
                em: ({node, ...props}) => <em className="italic text-gray-700" {...props} />,
                code: ({node, ...props}) => <code className="bg-gray-100 px-1 py-0.5 rounded text-sm font-mono text-gray-800" {...props} />,
              }}
            >
              {summary.content}
            </ReactMarkdown>
          </div>
        </div>
      )}

      {/* Empty State - Dataset loaded, no summary yet */}
      {!isLoading && !summary && !error && fileId && (
        <div className="text-center py-12 bg-gradient-to-br from-blue-50 to-indigo-50 rounded-lg border-2 border-dashed border-blue-300">
          <div className="text-6xl mb-4">🔍</div>
          <h3 className="text-lg font-semibold text-gray-800 mb-2">
            Your data is being analyzed
          </h3>
          <p className="text-sm text-gray-600 mb-1">
            Dataset loaded successfully!
          </p>
          <p className="text-xs text-gray-500 mb-6">
            Start exploring your data or generate a summary now.
          </p>
          <button
            onClick={generateSummary}
            className="px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors shadow-md hover:shadow-lg"
          >
            ✨ Generate Summary Now
          </button>
        </div>
      )}

      {/* No dataset uploaded yet */}
      {!isLoading && !summary && !error && !fileId && (
        <div className="text-center py-12 bg-gray-50 rounded-lg border-2 border-dashed border-gray-300">
          <div className="text-4xl mb-4">📊</div>
          <p className="text-sm text-gray-600 mb-2">No dataset uploaded yet</p>
          <p className="text-xs text-gray-500">
            Upload a dataset to start analyzing
          </p>
        </div>
      )}
    </div>
  )
}

export default SummaryTab
