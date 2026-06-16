import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import axios from 'axios'
import FileCard from '../components/FileCard'

function DataPage() {
  const [files, setFiles] = useState([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetchFiles()
  }, [])

  const fetchFiles = async () => {
    try {
      setLoading(true)
      const response = await axios.get('/api/files')
      setFiles(response.data.files || [])
    } catch (error) {
      console.error('Failed to fetch files:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleFileDeleted = (fileId) => {
    setFiles(files.filter(f => f.id !== fileId))
  }

  const formatFileSize = (bytes) => {
    if (bytes < 1024) return bytes + ' B'
    else if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB'
    else return (bytes / (1024 * 1024)).toFixed(2) + ' MB'
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-6 py-6">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold text-gray-800">Data Library</h1>
              <p className="text-gray-600 mt-1">Manage your uploaded CSV files</p>
            </div>
            <Link
              to="/"
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors"
            >
              Upload in Feed →
            </Link>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-7xl mx-auto px-6 py-8">
        {loading ? (
          <div className="text-center py-16">
            <div className="text-4xl mb-4">⏳</div>
            <p className="text-gray-500">Loading...</p>
          </div>
        ) : files.length === 0 ? (
          <div className="bg-white rounded-lg shadow p-16 text-center">
            <div className="text-6xl mb-4">📂</div>
            <h2 className="text-2xl font-semibold text-gray-700 mb-2">
              No files uploaded yet
            </h2>
            <p className="text-gray-500 mb-6">
              Attach CSV files when creating posts in the Feed to upload them.
            </p>
            <Link
              to="/"
              className="inline-flex items-center px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors"
            >
              Go to Feed →
            </Link>
          </div>
        ) : (
          <>
            {/* Stats */}
            <div className="grid grid-cols-3 gap-6 mb-8">
              <div className="bg-white rounded-lg shadow p-6">
                <div className="text-3xl mb-2">📊</div>
                <div className="text-2xl font-bold text-gray-800">{files.length}</div>
                <div className="text-sm text-gray-500">Total Files</div>
              </div>
              <div className="bg-white rounded-lg shadow p-6">
                <div className="text-3xl mb-2">📏</div>
                <div className="text-2xl font-bold text-gray-800">
                  {files.reduce((sum, f) => sum + f.rows, 0).toLocaleString()}
                </div>
                <div className="text-sm text-gray-500">Total Rows</div>
              </div>
              <div className="bg-white rounded-lg shadow p-6">
                <div className="text-3xl mb-2">💾</div>
                <div className="text-2xl font-bold text-gray-800">
                  {formatFileSize(files.reduce((sum, f) => sum + f.file_size, 0))}
                </div>
                <div className="text-sm text-gray-500">Total Size</div>
              </div>
            </div>

            {/* File List */}
            <div className="space-y-4">
              {files.map((file) => (
                <FileCard
                  key={file.id}
                  file={file}
                  onFileDeleted={handleFileDeleted}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default DataPage

