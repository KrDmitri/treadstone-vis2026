import { Vega } from 'react-vega'

function ChatMessage({ message }) {
  const isUser = message.author_type === 'user'
  const isAgent = message.author_type === 'agent'

  const getAgentIcon = (authorRole) => {
    switch (authorRole) {
      case 'statistics':
        return '📊' // Statistical Analyst
      case 'visualization':
        return '🎨' // Visualization Expert
      case 'insight':
        return '💡' // Intelligence
      case 'summary':
        return '📝' // Summary Agent
      default:
        return '🤖' // Default agent
    }
  }

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`flex max-w-3xl ${isUser ? 'flex-row-reverse' : 'flex-row'} space-x-3`}>
        {/* Avatar */}
        <div className="flex-shrink-0">
          <div className={`w-10 h-10 rounded-full flex items-center justify-center text-xl ${isAgent ? 'bg-blue-100' : 'bg-green-100'
            }`}>
            {isAgent ? getAgentIcon(message.author_role) : '👤'}
          </div>
        </div>

        {/* Message Content */}
        <div className={`flex flex-col ${isUser ? 'items-end mr-3' : 'items-start ml-3'}`}>
          {/* Author Info */}
          <div className={`flex items-center space-x-2 mb-1 ${isUser ? 'flex-row-reverse space-x-reverse' : ''}`}>
            <span className={`text-sm font-semibold ${isAgent ? 'text-blue-600' : 'text-green-600'
              }`}>
              {message.author}
            </span>
            {message.author_role && (
              <span className="text-xs text-gray-500">· {message.author_role}</span>
            )}
            <span className="text-xs text-gray-400">
              {new Date(message.created_at).toLocaleTimeString('en-US', {
                hour: '2-digit',
                minute: '2-digit'
              })}
            </span>
          </div>

          {/* Message Bubble */}
          <div className={`rounded-2xl px-4 py-3 ${isUser
              ? 'bg-blue-500 text-white rounded-tr-none'
              : 'bg-white text-gray-800 border border-gray-200 rounded-tl-none'
            }`}>
            <p className="whitespace-pre-wrap break-words">{message.content}</p>
          </div>

          {/* File Attachment */}
          {message.file_metadata && (
            <div className="mt-3 p-3 bg-green-50 rounded-lg border border-green-200 max-w-md">
              <div className="flex items-center space-x-2">
                <span className="text-lg">{message.file_metadata.file_type === 'csv' ? '📊' : '📄'}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center space-x-1">
                    <span className="text-xs font-semibold text-green-800 truncate">
                      {message.file_metadata.original_filename}
                    </span>
                    <span className="px-1.5 py-0.5 bg-green-200 text-green-800 rounded text-xs flex-shrink-0">
                      {message.file_metadata.file_type?.toUpperCase() || 'FILE'}
                    </span>
                  </div>
                  <div className="flex items-center space-x-2 mt-1 text-xs text-green-700">
                    {message.file_metadata.file_type === 'csv' && message.file_metadata.rows && (
                      <>
                        <span>📏 {message.file_metadata.rows.toLocaleString()}</span>
                        <span>📊 {message.file_metadata.columns}</span>
                      </>
                    )}
                    {message.file_metadata.file_type === 'txt' && message.file_metadata.line_count && (
                      <span>📏 {message.file_metadata.line_count.toLocaleString()} lines</span>
                    )}
                    <span>💾 {((message.file_metadata.size || 0) / 1024).toFixed(1)}KB</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Visualization */}
          {message.visualization && (
            <div className="mt-3 p-4 bg-white rounded-lg border border-gray-200 shadow-sm">
              <Vega spec={message.visualization} actions={false} />
            </div>
          )}

          {/* HITL Options */}
          {message.hitl_options && message.hitl_options.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {message.hitl_options.map((option, idx) => (
                <button
                  key={idx}
                  className="px-3 py-1.5 bg-white hover:bg-blue-50 text-blue-600 border border-blue-200 rounded-full text-sm transition-colors"
                >
                  {option}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default ChatMessage
