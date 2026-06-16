import { useState, useEffect } from 'react'
import axios from 'axios'
import { getClientId } from '../utils/clientId'

function NextStepsCarousel({ steps, onPostCreated, fileId, posts = [], variant = 'bottom', segmentPostIds = [], segmentIndex = 0, clientId: propClientId }) {
    // Use prop clientId if provided, otherwise get from utility
    const clientId = propClientId || getClientId()
    const [displayedSteps, setDisplayedSteps] = useState([])
    const [clickedIds, setClickedIds] = useState(new Set())
    const [isCreating, setIsCreating] = useState(null)
    const [isRefreshing, setIsRefreshing] = useState(false)
    const [isLoading, setIsLoading] = useState(false)

    // Variant-specific settings
    const isInline = variant === 'inline'
    const maxCards = isInline ? 3 : 5

    // Fetch segment-based steps for inline variant
    const fetchSegmentSteps = async () => {
        if (!fileId || !isInline || segmentPostIds.length === 0) return

        try {
            setIsLoading(true)

            const response = await axios.post(`/api/next-steps/${fileId}/segment?client_id=${encodeURIComponent(clientId)}`, {
                post_ids: segmentPostIds,
                segment_index: segmentIndex,
                count: maxCards
            })

            const newSteps = response.data.next_steps || []
            setDisplayedSteps(newSteps)
        } catch (err) {
            console.error(` Failed to fetch segment steps:`, err)
            // Fallback to regular steps if provided
            if (steps && steps.length > 0) {
                setDisplayedSteps(steps.slice(0, maxCards))
            }
        } finally {
            setIsLoading(false)
        }
    }

    // For inline variant: fetch segment-specific steps on mount
    useEffect(() => {
        if (isInline && segmentPostIds.length > 0) {
            fetchSegmentSteps()
        } else if (steps && steps.length > 0) {
            // For bottom variant or fallback: use provided steps
            setDisplayedSteps(steps.slice(0, maxCards))
        }
    }, [isInline, segmentPostIds.join(','), fileId])

    // For bottom variant: fetch from contextual API if no steps provided
    useEffect(() => {
        if (!isInline && (!steps || steps.length === 0) && fileId) {
            fetchContextualSteps(new Set(), maxCards).then(newSteps => {
                if (newSteps.length > 0) {
                    setDisplayedSteps(newSteps)
                }
            })
        }
    }, [isInline, fileId])

    const fetchContextualSteps = async (excludeIds, count = 3) => {
        if (!fileId) return []

        try {
            const response = await axios.post(`/api/next-steps/${fileId}/contextual?client_id=${encodeURIComponent(clientId)}`, {
                exclude_ids: Array.from(excludeIds),
                count: count
            })
            return response.data.next_steps || []
        } catch (err) {
            console.error('Failed to fetch contextual next steps:', err)
            // Fallback to regular next steps
            try {
                const excludeParam = Array.from(excludeIds).join(',')
                const fallbackResponse = await axios.get(`/api/next-steps/${fileId}?client_id=${encodeURIComponent(clientId)}&exclude_ids=${excludeParam}`)
                return (fallbackResponse.data.next_steps || []).slice(0, count)
            } catch (fallbackErr) {
                console.error('Fallback also failed:', fallbackErr)
                return []
            }
        }
    }

    const handleCardClick = async (step) => {
        if (clickedIds.has(step.id) || isCreating) return

        try {
            setIsCreating(step.id)

            // Create a post with the question
            const formData = new FormData()
            formData.append('client_id', clientId)  // Required for client isolation
            formData.append('author', 'You')
            formData.append('author_type', 'user')
            formData.append('content', step.question)
            const targetAgentRole = step.target_agent_role || step.agent_role
            if (targetAgentRole) {
                formData.append('target_agent_role', targetAgentRole)
            }

            // Link to source post for provenance tracking in Branch View
            const sourcePostId = segmentPostIds.length > 0
                ? segmentPostIds[segmentPostIds.length - 1]
                : (posts.length > 0 ? posts[posts.length - 1].id : null)
            if (sourcePostId) {
                formData.append('references_post_id', sourcePostId)
            }

            const response = await axios.post('/api/post', formData)

            if (response.status === 200) {

                // Mark as clicked
                const newClickedIds = new Set([...clickedIds, step.id])
                setClickedIds(newClickedIds)

                // Remove clicked card
                const remaining = displayedSteps.filter(s => s.id !== step.id)

                // Keep 2 cards for inline, more for bottom
                const keepCount = isInline ? 1 : 2
                const keptSteps = remaining.slice(0, keepCount)
                const removedSteps = remaining.slice(keepCount)

                // Collect all excluded IDs
                const allExcludedIds = new Set([
                    ...newClickedIds,
                    ...removedSteps.map(s => s.id)
                ])

                // Fetch new contextual cards
                const fetchCount = isInline ? 2 : 3
                const newSteps = await fetchContextualSteps(allExcludedIds, fetchCount)

                // Update cards
                setDisplayedSteps([...keptSteps, ...newSteps].slice(0, maxCards))

                // Notify parent about new post
                if (onPostCreated) {
                    onPostCreated(response.data)
                }
            }
        } catch (err) {
            console.error('Failed to create post from next step:', err)
        } finally {
            setIsCreating(null)
        }
    }

    const handleManualRefresh = async () => {
        if (isRefreshing) return

        try {
            setIsRefreshing(true)

            // Exclude all current cards and clicked cards
            const allCurrentIds = new Set([
                ...clickedIds,
                ...displayedSteps.map(s => s.id)
            ])

            // Fetch new cards based on variant
            const newSteps = await fetchContextualSteps(allCurrentIds, maxCards)

            if (newSteps.length > 0) {
                setDisplayedSteps(newSteps)
            } else {
                console.warn('No new steps available')
            }
        } catch (err) {
            console.error('Failed to refresh next steps:', err)
        } finally {
            setIsRefreshing(false)
        }
    }

    // Show loading state for inline variant
    if (isLoading && isInline) {
        return (
            <div className="my-4 px-1">
                <div className="flex items-center space-x-2 mb-3">
                    <span className="text-xl">✨</span>
                    <h3 className="text-sm font-semibold text-gray-600">Loading suggestions...</h3>
                </div>
                <div className="flex gap-4 overflow-hidden">
                    {[1, 2, 3].map(i => (
                        <div key={i} className="flex-shrink-0 w-64 h-32 bg-gray-100 rounded-xl animate-pulse" />
                    ))}
                </div>
            </div>
        )
    }

    if (!displayedSteps || displayedSteps.length === 0) {
        return null
    }

    return (
        <div className={isInline ? "my-4" : "my-8"}>
            {/* Header */}
            <div className="flex items-center justify-between px-1 mb-3">
                <div className="flex items-center space-x-2">
                    <span className="text-xl">✨</span>
                    <h3 className="text-base font-semibold text-gray-800">Suggested For You</h3>
                </div>

                {/* Manual Refresh Button */}
                <button
                    onClick={handleManualRefresh}
                    disabled={isRefreshing}
                    className={`
                        p-2 rounded-lg transition-all duration-200
                        ${isRefreshing
                            ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                            : 'bg-white border border-gray-200 text-gray-600 hover:bg-gray-50 hover:border-blue-300 hover:text-blue-600'
                        }
                    `}
                    title="Refresh suggestions"
                >
                    <svg
                        className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`}
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                    >
                        <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                        />
                    </svg>
                </button>
            </div>

            {/* Carousel Container */}
            <div
                className="flex overflow-x-auto gap-4 pb-4 px-1 snap-x snap-mandatory scrollbar-hide"
                style={{
                    scrollbarWidth: 'none',
                    msOverflowStyle: 'none',
                    WebkitOverflowScrolling: 'touch'
                }}
            >
                {displayedSteps.map((step, index) => {
                    const isClicked = clickedIds.has(step.id)
                    const isLoading = isCreating === step.id

                    return (
                        <div
                            key={step.id}
                            onClick={() => handleCardClick(step)}
                            className={`
                                flex-shrink-0 w-80 snap-start
                                bg-white border border-gray-200 rounded-xl
                                shadow-sm hover:shadow-md hover:border-blue-300
                                cursor-pointer transition-all duration-300
                                relative overflow-hidden group
                                ${isClicked ? 'opacity-50 scale-95' : 'active:scale-[0.98]'}
                                ${isLoading ? 'animate-pulse' : ''}
                            `}
                        >
                            <div className="p-5 h-40 flex flex-col justify-between">
                                {/* Top Section: Icon & Title */}
                                <div className="flex items-start gap-3">
                                    <div className={`
                                        flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center text-xl
                                        ${index % 3 === 0 ? 'bg-blue-50 text-blue-600' :
                                            index % 3 === 1 ? 'bg-purple-50 text-purple-600' :
                                                'bg-emerald-50 text-emerald-600'}
                                    `}>
                                        {step.icon}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <h4 className="font-semibold text-gray-900 leading-tight line-clamp-2 group-hover:text-blue-600 transition-colors">
                                            {step.title}
                                        </h4>
                                    </div>
                                </div>

                                {/* Bottom Section: Description & Action */}
                                <div>
                                    <p className="text-sm text-gray-500 line-clamp-2 mb-3">
                                        {step.description}
                                    </p>

                                    {!isClicked && !isLoading && (
                                        <div className="flex items-center text-xs font-medium text-blue-600 opacity-0 group-hover:opacity-100 transition-opacity transform translate-y-1 group-hover:translate-y-0 duration-300">
                                            Explore topic <span className="ml-1">→</span>
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* Hover: Full text overlay inside card */}
                            {!isClicked && !isLoading && (
                                <div className="absolute inset-0 bg-white rounded-xl p-5 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-200 z-20 overflow-y-auto">
                                    <p className="font-semibold text-blue-600 text-sm leading-snug mb-2">{step.title}</p>
                                    <p className="text-gray-600 text-xs leading-relaxed mb-3">{step.description}</p>
                                    <div className="flex items-center text-xs font-medium text-blue-600">
                                        Click to explore <span className="ml-1">→</span>
                                    </div>
                                </div>
                            )}

                            {/* Loading Overlay */}
                            {isLoading && (
                                <div className="absolute inset-0 bg-white/80 flex items-center justify-center z-10">
                                    <div className="flex flex-col items-center">
                                        <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mb-2"></div>
                                        <span className="text-xs text-blue-600 font-medium">Creating...</span>
                                    </div>
                                </div>
                            )}

                            {/* Success Overlay */}
                            {isClicked && (
                                <div className="absolute inset-0 bg-gray-50/50 flex items-center justify-center z-10">
                                    <div className="flex flex-col items-center text-gray-400">
                                        <span className="text-2xl mb-1">✓</span>
                                        <span className="text-xs font-medium">Added to feed</span>
                                    </div>
                                </div>
                            )}
                        </div>
                    )
                })}
            </div>
        </div>
    )
}

export default NextStepsCarousel
