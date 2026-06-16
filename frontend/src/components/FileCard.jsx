import { useState } from 'react'
import axios from 'axios'
import { getClientId } from '../utils/clientId'

function FileCard({ file, onFileDeleted }) {
  const clientId = getClientId()
  const [showPreview, setShowPreview] = useState(false)
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const formatFileSize = (bytes) => {
    if (bytes < 1024) return bytes + ' B'
    else if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB'
    else return (bytes / (1024 * 1024)).toFixed(2) + ' MB'
  }

  const formatDate = (dateString) => {
    return new Date(dateString).toLocaleString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    })
  }

  const handlePreview = async () => {
    if (showPreview) {
      setShowPreview(false)
      return
    }

    try {
      setLoading(true)
      const response = await axios.get(`/api/files/${file.file_id}/preview?rows=5&client_id=${encodeURIComponent(clientId)}`)
      setPreview(response.data)
      setShowPreview(true)
    } catch (error) {
      console.error('Failed to load preview:', error)
      alert('Failed to load preview.')
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async () => {
    if (!confirm(`Delete file "${file.original_filename}"?`)) {
      return
    }

    try {
      setDeleting(true)
      await axios.delete(`/api/files/${file.file_id}/delete`)
      onFileDeleted(file.file_id)
    } catch (error) {
      console.error('Failed to delete file:', error)
      alert('Failed to delete file.')
    } finally {
      setDeleting(false)
    }
  }

  const isCSV = file.file_type === 'csv'
  const isTXT = file.file_type === 'txt'

  return (
    <div className="bg-white rounded-lg shadow border border-gray-200 overflow-hidden">
      {/* File Info */}
      <div className="p-6">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center space-x-3 mb-2">
              <span className="text-3xl">{isCSV ? '📊' : '📄'}</span>
              <div>
                <h3 className="text-lg font-semibold text-gray-800">
                  {file.original_filename}
                </h3>
                <p className="text-sm text-gray-500">
                  Uploaded: {formatDate(file.uploaded_at)}
                </p>
              </div>
            </div>

            <div className="flex items-center space-x-4 mt-4">
              {isCSV && (
                <>
                  <div className="px-3 py-1 bg-blue-50 text-blue-700 rounded-full text-sm">
                    📏 {file.rows?.toLocaleString() || 0} rows
                  </div>
                  <div className="px-3 py-1 bg-green-50 text-green-700 rounded-full text-sm">
                    📊 {file.columns || 0} cols
                  </div>
                </>
              )}
              {isTXT && (
                <div className="px-3 py-1 bg-blue-50 text-blue-700 rounded-full text-sm">
                  📏 {file.line_count?.toLocaleString() || 0} lines
                </div>
              )}
              <div className="px-3 py-1 bg-purple-50 text-purple-700 rounded-full text-sm">
                💾 {formatFileSize(file.size || 0)}
              </div>
            </div>

            {isCSV && file.column_names && (
              <div className="mt-3">
                <p className="text-sm text-gray-600">
                  <span className="font-medium">Columns:</span>{' '}
                  {file.column_names.slice(0, 5).join(', ')}
                  {file.column_names.length > 5 && ` +${file.column_names.length - 5} more`}
                </p>
              </div>
            )}
          </div>

          <div className="flex flex-col space-y-2">
            <button
              onClick={handlePreview}
              disabled={loading}
              className="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
            >
              {loading ? 'Loading...' : showPreview ? 'Close' : 'Preview'}
            </button>
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="px-4 py-2 bg-red-50 hover:bg-red-100 text-red-600 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
            >
              {deleting ? 'Deleting...' : 'Delete'}
            </button>
          </div>
        </div>
      </div>

      {/* Preview */}
      {showPreview && preview && (
        <div className="border-t border-gray-200 p-6 bg-gray-50">
          {isCSV && preview.columns && preview.data && (
            <>
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-sm font-semibold text-gray-700">
                  Data Preview (Top 5 rows, {preview.total_columns || preview.columns.length} columns)
                </h4>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200 text-sm">
                  <thead className="bg-gray-100">
                    <tr>
                      {preview.columns.map((col, idx) => (
                        <th
                          key={idx}
                          className="px-4 py-2 text-left text-xs font-medium text-gray-700 uppercase tracking-wider"
                        >
                          {col}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-200">
                    {preview.data.map((row, rowIdx) => (
                      <tr key={rowIdx}>
                        {preview.columns.map((col, cellIdx) => (
                          <td key={cellIdx} className="px-4 py-2 whitespace-nowrap text-gray-800">
                            {row[col] === null || row[col] === undefined ? (
                              <span className="text-gray-400 italic">null</span>
                            ) : (
                              String(row[col])
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-gray-500 mt-3">
                Showing 5 rows of {preview.total_rows?.toLocaleString() || file.rows?.toLocaleString() || 0} total
              </p>
            </>
          )}
          
          {isTXT && preview.lines && (
            <>
              <h4 className="text-sm font-semibold text-gray-700 mb-3">
                Text Preview (Top 5 lines)
              </h4>
              <div className="bg-white rounded border border-gray-200 p-4">
                <pre className="text-sm text-gray-800 whitespace-pre-wrap font-mono">
                  {preview.lines.join('\n')}
                </pre>
              </div>
              <p className="text-xs text-gray-500 mt-3">
                Showing 5 lines of {preview.total_lines?.toLocaleString() || file.line_count?.toLocaleString() || 0} total
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default FileCard
