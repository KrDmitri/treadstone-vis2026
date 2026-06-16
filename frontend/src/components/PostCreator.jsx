import { useState, useRef } from 'react'
import axios from 'axios'
import { getClientId } from '../utils/clientId'

function PostCreator({ onPostCreated, lastFileId, clientId: propClientId }) {
  // Use prop clientId if provided, otherwise get from utility
  const clientId = propClientId || getClientId()
  const [content, setContent] = useState('')
  const [file, setFile] = useState(null)
  const [image, setImage] = useState(null)
  const [imagePreview, setImagePreview] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [isDragOver, setIsDragOver] = useState(false)
  const dropZoneRef = useRef(null)

  const handleImageSelect = (selectedFile) => {
    if (!selectedFile) return

    const imageTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
    if (!imageTypes.includes(selectedFile.type)) {
      alert('Please select a valid image file (JPG, PNG, GIF, or WebP)')
      return
    }

    setImage(selectedFile)

    // Create preview URL
    const reader = new FileReader()
    reader.onload = (e) => {
      setImagePreview(e.target.result)
    }
    reader.readAsDataURL(selectedFile)
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(true)
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)

    const droppedFiles = e.dataTransfer.files
    if (droppedFiles.length > 0) {
      const droppedFile = droppedFiles[0]

      // Check if it's an image
      if (droppedFile.type.startsWith('image/')) {
        handleImageSelect(droppedFile)
      }
      // Check if it's a data file (CSV/TXT)
      else if (droppedFile.name.endsWith('.csv') || droppedFile.name.endsWith('.txt')) {
        setFile(droppedFile)
      }
      else {
        alert('Please drop an image (JPG, PNG, GIF, WebP) or data file (CSV, TXT)')
      }
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()

    if (!content.trim()) {
      alert('Please enter content.')
      return
    }

    // OPTIMISTIC UPDATE: Show user post immediately
    const optimisticPost = {
      id: Date.now(), // Temporary ID
      author: 'User1',
      author_type: 'user',
      author_role: null,
      content: content,
      created_at: new Date().toISOString(),
      likes: 0,
      replies: [],
      visualization: null,
      hitl_options: null,
      file_metadata: null, // Will be updated after backend response
      references_post_id: null,
      image_preview: imagePreview // For immediate display
    }
    onPostCreated(optimisticPost)

    // Clear form immediately for better UX
    const currentContent = content
    const currentFile = file
    const currentImage = image
    const currentImagePreview = imagePreview // Save for later use in response
    setContent('')
    setFile(null)
    setImage(null)
    setImagePreview(null)

    try {
      setSubmitting(true)

      const formData = new FormData()
      formData.append('client_id', clientId)  // Required for client isolation
      formData.append('content', currentContent)
      formData.append('author', 'User1')
      formData.append('author_type', 'user')

      // Attach data file OR image
      if (currentImage) {
        formData.append('file', currentImage)
      } else if (currentFile) {
        formData.append('file', currentFile)
      }

      // Create user post in backend
      const response = await axios.post('/api/post', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      })

      // If file was uploaded, update the post with file_metadata
      // Keep image_preview as fallback until image_url is ready
      if (response.data.file_metadata) {
        onPostCreated({
          ...response.data,
          id: optimisticPost.id,
          image_preview: currentImagePreview // Preserve preview for immediate display
        })
      }

      // New file uploaded → Trigger PROACTIVE scan (initial)
      const isNewFileUploaded = response.data.file_metadata?.file_id && currentFile

      if (isNewFileUploaded) {
        const newFileId = response.data.file_metadata.file_id

        try {
          const proactiveScanResponse = await axios.post('/api/agent/proactive-scan', {
            file_id: newFileId,
            client_id: clientId  // Required for client isolation
          })


          // Create proactive POST
          const proactiveFormData = new FormData()
          proactiveFormData.append('client_id', clientId)  // Required for client isolation
          proactiveFormData.append('content', proactiveScanResponse.data.content)
          proactiveFormData.append('author', proactiveScanResponse.data.author)
          proactiveFormData.append('author_type', proactiveScanResponse.data.author_type)
          proactiveFormData.append('author_role', proactiveScanResponse.data.author_role)

          if (proactiveScanResponse.data.visualization) {
            proactiveFormData.append('visualization', JSON.stringify(proactiveScanResponse.data.visualization))
          }

          // CRITICAL: Pass file_id so backend can reference the file_metadata
          if (proactiveScanResponse.data.file_metadata?.file_id) {
            const fileIdToReference = proactiveScanResponse.data.file_metadata.file_id
            proactiveFormData.append('file_id', fileIdToReference)
          }

          const proactivePost = await axios.post('/api/post', proactiveFormData, {
            headers: {
              'Content-Type': 'multipart/form-data',
            },
          })

          onPostCreated(proactivePost.data)
        } catch (proactiveError) {
          console.error('Proactive scan failed:', proactiveError)
        }
      }
    } catch (error) {
      console.error('Failed to create post:', error)
      alert('Failed to create post. Your message may not have been saved.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      {/* Drag-and-drop zone wrapper */}
      <div
        ref={dropZoneRef}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`relative rounded-lg transition-all duration-200 ${isDragOver
          ? 'ring-2 ring-blue-500 ring-offset-2 bg-blue-50'
          : ''
          }`}
      >
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="What's on your mind? Ask about data analysis... (Drop images or files here)"
          className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none"
          rows="3"
          disabled={submitting}
        />

        {/* Drag overlay */}
        {isDragOver && (
          <div className="absolute inset-0 flex items-center justify-center bg-blue-50/90 rounded-lg border-2 border-dashed border-blue-400">
            <div className="text-center">
              <span className="text-3xl">📥</span>
              <p className="text-sm font-medium text-blue-600 mt-1">Drop image or data file here</p>
            </div>
          </div>
        )}
      </div>

      {/* Image Preview */}
      {imagePreview && (
        <div className="relative inline-block">
          <img
            src={imagePreview}
            alt="Preview"
            className="h-20 w-auto rounded-lg border border-gray-200 shadow-sm"
          />
          <button
            type="button"
            onClick={() => {
              setImage(null)
              setImagePreview(null)
            }}
            className="absolute -top-2 -right-2 w-5 h-5 bg-red-500 text-white rounded-full text-xs flex items-center justify-center hover:bg-red-600 shadow"
          >
            ✕
          </button>
          <span className="text-xs text-gray-500 ml-2">{image?.name}</span>
        </div>
      )}

      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-2">
          {/* Data File Button */}
          <label className="cursor-pointer">
            <input
              type="file"
              accept=".csv,.txt"
              onChange={(e) => setFile(e.target.files[0])}
              className="hidden"
              disabled={submitting}
            />
            <span className="inline-flex items-center px-3 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-sm transition-colors">
              📊 Data File
            </span>
          </label>

          {/* Image Button */}
          <label className="cursor-pointer">
            <input
              type="file"
              accept="image/jpeg,image/png,image/gif,image/webp"
              onChange={(e) => handleImageSelect(e.target.files[0])}
              className="hidden"
              disabled={submitting}
            />
            <span className="inline-flex items-center px-3 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-sm transition-colors">
              🖼️ Image
            </span>
          </label>

          {/* File name display */}
          {file && !image && (
            <span className="text-sm text-gray-600">
              {file.name}
              <button
                type="button"
                onClick={() => setFile(null)}
                className="ml-2 text-red-500 hover:text-red-700"
              >
                ✕
              </button>
            </span>
          )}
        </div>

        <button
          type="submit"
          disabled={submitting || !content.trim()}
          className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors disabled:bg-gray-300 disabled:cursor-not-allowed"
        >
          {submitting ? 'Posting...' : 'Post'}
        </button>
      </div>
    </form>
  )
}

export default PostCreator
