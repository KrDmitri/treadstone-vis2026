import { useState } from 'react'
import { Vega } from 'react-vega'

function MediaGallery({ charts = [], images = [], filter = 'all' }) {
    const [selectedItem, setSelectedItem] = useState(null)

    // Filter items based on current filter
    const filteredCharts = filter === 'all' || filter === 'charts' ? charts : []
    const filteredImages = filter === 'all' || filter === 'images' ? images : []

    const hasContent = filteredCharts.length > 0 || filteredImages.length > 0

    if (!hasContent) {
        return (
            <div className="text-center py-8 text-gray-500">
                <div className="text-4xl mb-2">🖼️</div>
                <p>No media yet</p>
                <p className="text-sm">Charts and images will appear here</p>
            </div>
        )
    }

    const handleItemClick = (item) => {
        setSelectedItem(item)
    }

    const closeModal = () => {
        setSelectedItem(null)
    }

    // Create thumbnail-sized spec for charts
    const getThumbnailSpec = (chartData) => {
        if (!chartData) return null

        try {
            // Clone the spec and adjust size for thumbnail
            const spec = typeof chartData === 'string' ? JSON.parse(chartData) : { ...chartData }
            return {
                ...spec,
                width: 200,
                height: 120,
                autosize: { type: 'fit', contains: 'padding' },
                // Disable interactions for thumbnail
                config: {
                    ...spec.config,
                    axis: { labelFontSize: 8, titleFontSize: 9 },
                    legend: { labelFontSize: 8, titleFontSize: 9 }
                }
            }
        } catch (e) {
            console.error('Failed to parse chart spec:', e)
            return null
        }
    }

    // Create full-size spec for modal
    const getFullSpec = (chartData) => {
        if (!chartData) return null

        try {
            const spec = typeof chartData === 'string' ? JSON.parse(chartData) : { ...chartData }
            return {
                ...spec,
                width: 600,
                height: 400,
                autosize: { type: 'fit', contains: 'padding' }
            }
        } catch (e) {
            console.error('Failed to parse chart spec:', e)
            return null
        }
    }

    return (
        <>
            {/* Charts Section */}
            {filteredCharts.length > 0 && (
                <div className="mb-6">
                    <h3 className="text-sm font-semibold text-gray-600 mb-3 flex items-center gap-2">
                        📊 Generated Charts ({filteredCharts.length})
                    </h3>
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                        {filteredCharts.map((chart) => {
                            const thumbnailSpec = getThumbnailSpec(chart.chart_data)

                            return (
                                <div
                                    key={chart.id}
                                    onClick={() => handleItemClick(chart)}
                                    className="bg-white rounded-lg shadow hover:shadow-lg transition-shadow cursor-pointer overflow-hidden group"
                                >
                                    {/* Chart Thumbnail */}
                                    <div className="aspect-video bg-gradient-to-br from-blue-50 to-indigo-50 flex items-center justify-center p-2 overflow-hidden">
                                        {thumbnailSpec ? (
                                            <div className="transform scale-90 pointer-events-none">
                                                <Vega spec={thumbnailSpec} actions={false} />
                                            </div>
                                        ) : (
                                            <div className="text-4xl">📊</div>
                                        )}
                                    </div>
                                    {/* Chart Info */}
                                    <div className="p-3">
                                        <h4 className="text-sm font-medium text-gray-800 truncate">
                                            {chart.title}
                                        </h4>
                                        <p className="text-xs text-gray-500 mt-1 truncate">
                                            {chart.source} • {chart.author}
                                        </p>
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                </div>
            )}

            {/* Images Section */}
            {filteredImages.length > 0 && (
                <div className="mb-6">
                    <h3 className="text-sm font-semibold text-gray-600 mb-3 flex items-center gap-2">
                        🖼️ Uploaded Images ({filteredImages.length})
                    </h3>
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                        {filteredImages.map((image) => (
                            <div
                                key={image.id}
                                onClick={() => handleItemClick(image)}
                                className="bg-white rounded-lg shadow hover:shadow-lg transition-shadow cursor-pointer overflow-hidden group"
                            >
                                {/* Image Thumbnail */}
                                <div className="aspect-video bg-gray-100 flex items-center justify-center overflow-hidden">
                                    <img
                                        src={image.image_url}
                                        alt={image.title}
                                        className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                                        onError={(e) => {
                                            e.target.style.display = 'none'
                                            e.target.parentElement.innerHTML = '<div class="text-4xl">🖼️</div>'
                                        }}
                                    />
                                </div>
                                {/* Image Info */}
                                <div className="p-3">
                                    <h4 className="text-sm font-medium text-gray-800 truncate">
                                        {image.title}
                                    </h4>
                                    <p className="text-xs text-gray-500 mt-1">
                                        {image.source} • {image.width && image.height ? `${image.width}×${image.height}` : 'Image'}
                                    </p>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Modal for full-size view */}
            {selectedItem && (
                <div
                    className="fixed inset-0 bg-black bg-opacity-75 z-50 flex items-center justify-center p-4"
                    onClick={closeModal}
                >
                    <div
                        className="bg-white rounded-lg max-w-4xl max-h-[90vh] overflow-auto"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Modal Header */}
                        <div className="flex items-center justify-between p-4 border-b">
                            <div>
                                <h3 className="font-semibold text-gray-800">{selectedItem.title}</h3>
                                <p className="text-sm text-gray-500">
                                    {selectedItem.source} • {selectedItem.author}
                                </p>
                            </div>
                            <button
                                onClick={closeModal}
                                className="text-gray-400 hover:text-gray-600 text-2xl"
                            >
                                ×
                            </button>
                        </div>

                        {/* Modal Content */}
                        <div className="p-4">
                            {selectedItem.type === 'image' ? (
                                <img
                                    src={selectedItem.image_url}
                                    alt={selectedItem.title}
                                    className="max-w-full max-h-[70vh] mx-auto"
                                />
                            ) : (
                                <div className="flex justify-center">
                                    {getFullSpec(selectedItem.chart_data) ? (
                                        <Vega spec={getFullSpec(selectedItem.chart_data)} actions={false} />
                                    ) : (
                                        <div className="text-center py-12 bg-gradient-to-br from-blue-50 to-indigo-100 rounded-lg w-full">
                                            <div className="text-6xl mb-4">📊</div>
                                            <h4 className="text-xl font-semibold">{selectedItem.title}</h4>
                                            <p className="text-gray-600 mt-2">Chart Type: {selectedItem.chart_type}</p>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </>
    )
}

export default MediaGallery
