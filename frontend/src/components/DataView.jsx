import { useState, useEffect } from 'react'
import axios from 'axios'
import FileCard from './FileCard'
import MediaGallery from './MediaGallery'
import { getClientId } from '../utils/clientId'

function DataView() {
  const clientId = getClientId()
  const [files, setFiles] = useState([])
  const [media, setMedia] = useState({ charts: [], images: [] })
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('all') // 'all', 'charts', 'images', 'data'

  useEffect(() => {
    fetchData()
  }, [])

  const fetchData = async () => {
    try {
      setLoading(true)

      // Fetch files and media in parallel
      const [filesRes, mediaRes] = await Promise.all([
        axios.get('/api/files', { params: { client_id: clientId } }),
        axios.get('/api/media', { params: { client_id: clientId } })
      ])

      // Filter out image files from data files
      const imageTypes = ['jpg', 'jpeg', 'png', 'gif', 'webp']
      const dataFiles = (filesRes.data || []).filter(file => {
        const fileType = file.file_type?.toLowerCase()
        return !imageTypes.includes(fileType)
      })

      setFiles(dataFiles)
      setMedia(mediaRes.data || { charts: [], images: [] })
    } catch (error) {
      console.error('Failed to fetch data:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleFileDeleted = (fileId) => {
    setFiles(files.filter(f => f.file_id !== fileId))
  }

  const formatFileSize = (bytes) => {
    if (bytes < 1024) return bytes + ' B'
    else if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB'
    else return (bytes / (1024 * 1024)).toFixed(2) + ' MB'
  }

  const filters = [
    { id: 'all', label: 'All', icon: '📁' },
    { id: 'charts', label: 'Charts', icon: '📊', count: media.total_charts },
    { id: 'images', label: 'Images', icon: '🖼️', count: media.total_images },
    { id: 'data', label: 'Data Files', icon: '📄', count: files.length },
  ]

  const showMedia = filter === 'all' || filter === 'charts' || filter === 'images'
  const showDataFiles = filter === 'all' || filter === 'data'
  const hasAnyContent = files.length > 0 || media.charts?.length > 0 || media.images?.length > 0

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-xl font-semibold text-gray-800">Data Library</h2>
            <p className="text-sm text-gray-500">Your data files, charts, and images</p>
          </div>
        </div>

        {/* Filter Buttons */}
        <div className="flex gap-2">
          {filters.map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors flex items-center gap-1.5 ${filter === f.id
                  ? 'bg-blue-500 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
            >
              <span>{f.icon}</span>
              <span>{f.label}</span>
              {f.count !== undefined && (
                <span className={`text-xs px-1.5 py-0.5 rounded-full ${filter === f.id ? 'bg-blue-400' : 'bg-gray-200'
                  }`}>
                  {f.count}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="text-center py-16">
            <div className="text-4xl mb-4">⏳</div>
            <p className="text-gray-500">Loading...</p>
          </div>
        ) : !hasAnyContent ? (
          <div className="bg-white rounded-lg shadow p-16 text-center">
            <div className="text-6xl mb-4">📂</div>
            <h2 className="text-2xl font-semibold text-gray-700 mb-2">
              No content yet
            </h2>
            <p className="text-gray-500 mb-6">
              Upload data files, images, or generate charts in the Feed.
            </p>
          </div>
        ) : (
          <>
            {/* Stats */}
            <div className="grid grid-cols-4 gap-4 mb-6">
              <div className="bg-white rounded-lg shadow p-4">
                <div className="text-2xl mb-1">📊</div>
                <div className="text-xl font-bold text-gray-800">{media.total_charts || 0}</div>
                <div className="text-xs text-gray-500">Charts</div>
              </div>
              <div className="bg-white rounded-lg shadow p-4">
                <div className="text-2xl mb-1">🖼️</div>
                <div className="text-xl font-bold text-gray-800">{media.total_images || 0}</div>
                <div className="text-xs text-gray-500">Images</div>
              </div>
              <div className="bg-white rounded-lg shadow p-4">
                <div className="text-2xl mb-1">📄</div>
                <div className="text-xl font-bold text-gray-800">{files.length}</div>
                <div className="text-xs text-gray-500">Data Files</div>
              </div>
              <div className="bg-white rounded-lg shadow p-4">
                <div className="text-2xl mb-1">📏</div>
                <div className="text-xl font-bold text-gray-800">
                  {files.reduce((sum, f) => sum + (f.rows || f.line_count || 0), 0).toLocaleString()}
                </div>
                <div className="text-xs text-gray-500">Total Rows</div>
              </div>
            </div>

            {/* Media Gallery (Charts & Images) */}
            {showMedia && (media.charts?.length > 0 || media.images?.length > 0) && (
              <div className="mb-8">
                <MediaGallery
                  charts={media.charts}
                  images={media.images}
                  filter={filter}
                />
              </div>
            )}

            {/* Data Files Section */}
            {showDataFiles && files.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-gray-600 mb-3 flex items-center gap-2">
                  📄 Data Files ({files.length})
                </h3>
                <div className="space-y-4">
                  {files.map((file) => (
                    <FileCard
                      key={file.file_id}
                      file={file}
                      onFileDeleted={handleFileDeleted}
                    />
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export default DataView
