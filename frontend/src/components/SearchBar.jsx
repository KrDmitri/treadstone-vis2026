import { useState, useRef, useEffect } from 'react'
import axios from 'axios'

/**
 * SearchBar - Simple keyword search for posts and replies
 */
function SearchBar({ clientId, onResultClick }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [isOpen, setIsOpen] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const inputRef = useRef(null)
  const dropdownRef = useRef(null)

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Debounced search
  useEffect(() => {
    if (!query.trim()) {
      setResults([])
      setIsOpen(false)
      return
    }

    const timer = setTimeout(async () => {
      setIsLoading(true)
      try {
        const response = await axios.get('/api/search', {
          params: { q: query, client_id: clientId }
        })
        setResults(response.data.results || [])
        setIsOpen(true)
      } catch (err) {
        console.error('Search failed:', err)
        setResults([])
      } finally {
        setIsLoading(false)
      }
    }, 300) // 300ms debounce

    return () => clearTimeout(timer)
  }, [query, clientId])

  const handleResultClick = (result) => {
    setIsOpen(false)
    setQuery('')
    if (onResultClick) {
      onResultClick(result)
    }
  }

  // Agent role to icon mapping
  const AGENT_ICONS = {
    statistics: '📊',
    visualization: '📈', 
    insight: '💼',
    summary: '📝',
    scanner: '🔍'
  }

  const getAuthorIcon = (authorType, authorRole) => {
    if (authorType === 'user') return '👤'
    // For agents, use role-specific icon
    return AGENT_ICONS[authorRole] || '🤖'
  }

  const highlightQuery = (text) => {
    if (!query.trim()) return text
    const regex = new RegExp(`(${query})`, 'gi')
    const parts = text.split(regex)
    return parts.map((part, i) => 
      regex.test(part) ? (
        <mark key={i} className="bg-yellow-200 text-yellow-900 rounded px-0.5">{part}</mark>
      ) : part
    )
  }

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Search Input */}
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">
          🔍
        </span>
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => query.trim() && results.length > 0 && setIsOpen(true)}
          placeholder="Search posts..."
          className="w-48 pl-9 pr-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-gray-50 hover:bg-white transition-colors"
        />
        {isLoading && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 animate-spin">
            ⏳
          </span>
        )}
      </div>

      {/* Results Dropdown */}
      {isOpen && results.length > 0 && (
        <div className="absolute top-full left-0 mt-2 w-80 max-h-96 overflow-y-auto bg-white rounded-lg shadow-xl border border-gray-200 z-50">
          <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-100 bg-gray-50">
            {results.length} result{results.length !== 1 ? 's' : ''} for "{query}"
          </div>
          
          {results.map((result, idx) => (
            <button
              key={`${result.type}-${result.post_id}-${result.reply_id || idx}`}
              onClick={() => handleResultClick(result)}
              className="w-full px-3 py-2.5 text-left hover:bg-blue-50 border-b border-gray-50 last:border-0 transition-colors"
            >
              <div className="flex items-start gap-2">
                <span className="text-lg mt-0.5">{getAuthorIcon(result.author_type, result.author_role)}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-sm font-medium text-gray-800 truncate">
                      {result.author}
                    </span>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      result.type === 'post' 
                        ? 'bg-blue-100 text-blue-700' 
                        : 'bg-gray-100 text-gray-600'
                    }`}>
                      {result.type}
                    </span>
                  </div>
                  <p className="text-sm text-gray-600 line-clamp-2">
                    {highlightQuery(result.snippet)}
                  </p>
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* No Results */}
      {isOpen && query.trim() && results.length === 0 && !isLoading && (
        <div className="absolute top-full left-0 mt-2 w-64 bg-white rounded-lg shadow-xl border border-gray-200 z-50 px-4 py-6 text-center">
          <span className="text-3xl mb-2 block">🔍</span>
          <p className="text-sm text-gray-500">No results for "{query}"</p>
        </div>
      )}
    </div>
  )
}

export default SearchBar
