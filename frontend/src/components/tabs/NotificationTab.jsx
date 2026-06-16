import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Bell, Activity, TrendingUp, BarChart3, Clock, MessageSquare, ChevronRight, Check, X, ArrowRight, Plus, HelpCircle, Lightbulb, Search, FileText, Pin } from 'lucide-react';
import axios from 'axios';
import { getClientId } from '../../utils/clientId';

const NotificationTab = ({ lastReadTime, isActive, wsMessage, clientId: propClientId, activeNodeId, onNodeActive }) => {
  const clientId = propClientId || getClientId();
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [fileId, setFileId] = useState(null);
  const [selectedTags, setSelectedTags] = useState([]);  // Tag filter
  const [showSavedOnly, setShowSavedOnly] = useState(false);  // Saved filter
  const [hoveredItem, setHoveredItem] = useState(null);  // Hovered notification id
  const [pinnedItem, setPinnedItem] = useState(null);    // Pinned notification id (persists on click)
  const [connectionMap, setConnectionMap] = useState(new Map());  // id -> connections
  const prevNotificationCountRef = useRef(0);
  const fetchNotificationsRef = useRef(null);  // Ref for latest fetchNotifications
  const itemRefs = useRef({});  // Refs for each notification item (for scroll-into-view)

  // Agent icon mapping by role
  const AGENT_ICONS = {
    statistics: { icon: TrendingUp, color: 'bg-blue-100 text-blue-600' },
    visualization: { icon: BarChart3, color: 'bg-green-100 text-green-600' },
    business: { icon: Lightbulb, color: 'bg-purple-100 text-purple-600' },
    summary: { icon: FileText, color: 'bg-orange-100 text-orange-600' },
    scanner: { icon: Search, color: 'bg-orange-100 text-orange-600' },
  };

  // Agent icon mapping by name (fallback)
  const agentIcons = {
    'Statistical Analyst Agent': { icon: TrendingUp, color: 'bg-blue-100 text-blue-600', border: 'border-blue-200' },
    'Visualization Expert Agent': { icon: BarChart3, color: 'bg-green-100 text-green-600', border: 'border-green-200' },
    'Summary Agent': { icon: FileText, color: 'bg-orange-100 text-orange-600', border: 'border-orange-200' },
    'Proactive Agent': { icon: Activity, color: 'bg-purple-100 text-purple-600', border: 'border-purple-200' },
    'Data Scout Agent': { icon: Activity, color: 'bg-orange-100 text-orange-600', border: 'border-orange-200' }
  };

  // Connection type config (simplified icons for narrow space)
  const CONNECTION_CONFIG = {
    supports: { icon: Check, color: 'text-green-600', bg: 'bg-green-100', label: '✓' },
    contradicts: { icon: X, color: 'text-red-600', bg: 'bg-red-100', label: '✗' },
    answers: { icon: MessageSquare, color: 'text-blue-600', bg: 'bg-blue-100', label: '→' },
    extends: { icon: Plus, color: 'text-purple-600', bg: 'bg-purple-100', label: '+' },
    questions: { icon: HelpCircle, color: 'text-orange-600', bg: 'bg-orange-100', label: '?' },
  };

  // Tag configuration (matching PostCard)
  const TAG_CONFIG = {
    hypothesis: { label: 'Hypothesis', color: 'bg-purple-100 text-purple-700 border-purple-200' },
    evidence: { label: 'Evidence', color: 'bg-blue-100 text-blue-700 border-blue-200' },
    question: { label: 'Question', color: 'bg-orange-100 text-orange-700 border-orange-200' },
    todo: { label: 'To-Do', color: 'bg-green-100 text-green-700 border-green-200' },
    insight: { label: 'Insight', color: 'bg-yellow-100 text-yellow-700 border-yellow-200' }
  };

  // Get current file_id from latest post
  useEffect(() => {
    const fetchCurrentFileId = async () => {
      try {
        const response = await axios.get('/api/feed', { params: { client_id: clientId } });
        const posts = response.data.posts || [];

        // Find the latest post with file_metadata
        for (let i = posts.length - 1; i >= 0; i--) {
          const post = posts[i];
          if (post.file_metadata && post.file_metadata.file_id) {
            setFileId(post.file_metadata.file_id);
            break;
          }
        }
      } catch (err) {
        console.error('Failed to fetch file_id:', err);
      }
    };

    // Initial fetch
    fetchCurrentFileId();

    // Reduced polling frequency (15 seconds) since we have WebSocket
    const pollInterval = setInterval(() => {
      fetchCurrentFileId();
    }, 15000);

    return () => clearInterval(pollInterval);
  }, []);

  // Handle WebSocket messages for real-time updates
  useEffect(() => {
    if (!wsMessage) return;


    if (wsMessage.type === 'new_reply' || wsMessage.type === 'new_post') {
      // Refetch notifications when new content arrives
      // Use ref to avoid circular dependency
      if (fetchNotificationsRef.current) {
        fetchNotificationsRef.current();
      }
    }

    // Handle post_saved event - update isSaved status in real-time
    if (wsMessage.type === 'post_saved') {
      const { post_id, is_saved } = wsMessage;

      setNotifications(prev => prev.map(notif => {
        if (notif.postId === post_id) {
          return { ...notif, isSaved: is_saved };
        }
        return notif;
      }));
    }
  }, [wsMessage]);


  // Fetch notifications from feed and timeline (for connections)
  const fetchNotifications = useCallback(async () => {
    if (!clientId) return;


    try {
      setLoading(true);
      
      // Fetch both feed and timeline data in parallel
      const [feedResponse, timelineResponse] = await Promise.all([
        axios.get('/api/feed', { params: { client_id: clientId } }),
        axios.get('/api/timeline', { params: { client_id: clientId } }).catch((err) => {
          console.warn('Timeline fetch failed:', err.message);
          return { data: { items: [] } };
        })
      ]);
      
      const data = feedResponse.data;
      const timelineItems = timelineResponse.data.items || [];
      
      
      // Build connection map from timeline data (normalize target_id to targetId)
      const connMap = new Map();
      let totalConns = 0;
      timelineItems.forEach(item => {
        if (item.connections && item.connections.length > 0) {
          // Normalize connection format (API uses target_id, we use targetId)
          const normalizedConns = item.connections.map(c => ({
            targetId: c.target_id || c.targetId,
            relation: c.relation,
            confidence: c.confidence
          }));
          connMap.set(item.id, normalizedConns);
          totalConns += normalizedConns.length;
        }
      });
      if (connMap.size > 0) {
        connMap.forEach((conns, key) => {
        });
      }
      setConnectionMap(connMap);

      // Convert posts and replies to notifications
      const notifs = [];

      data.posts.forEach(post => {
        // Skip user posts, only show agent activities
        if (post.author !== 'user' && post.author !== 'system') {
          notifs.push({
            id: `post_${post.id}`,
            type: 'proactive_post',
            agent: post.author,
            authorRole: post.author_role,
            postId: post.id,
            content: post.content,
            timestamp: post.timestamp || post.created_at,
            hasChart: post.vega_spec !== null || post.visualization !== null,
            tags: post.tags || [],
            isSaved: post.is_saved || false
          });
        }

        // Add replies as notifications (only agent replies)
        if (post.replies) {
          post.replies.forEach(reply => {
            if (reply.author !== 'user' && reply.author !== 'system') {
              notifs.push({
                id: `reply_${post.id}_${reply.id}`,
                type: 'reply',
                agent: reply.author,
                authorRole: reply.author_role,
                postId: post.id,
                postContent: post.content,
                replyContent: reply.content,
                timestamp: reply.timestamp || reply.created_at,
                hasChart: reply.vega_spec !== null || reply.visualization !== null,
                tags: reply.tags || [],
                isSaved: post.is_saved || false  // Inherit from parent post
              });
            }
          });
        }
      });

      // Sort by timestamp (oldest first, newest at bottom)
      notifs.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

      setNotifications(notifs);
      prevNotificationCountRef.current = notifs.length;
    } catch (error) {
      console.error('Failed to fetch notifications:', error);
    } finally {
      setLoading(false);
    }
  }, [clientId]);
  
  // Get all items connected to a given item (for highlighting)
  const getConnectedIds = useCallback((itemId) => {
    const connected = new Set();
    const itemConnections = connectionMap.get(itemId) || [];
    itemConnections.forEach(conn => connected.add(conn.targetId));
    
    // Also find items that connect TO this item
    connectionMap.forEach((connections, sourceId) => {
      connections.forEach(conn => {
        if (conn.targetId === itemId) {
          connected.add(sourceId);
        }
      });
    });
    
    return connected;
  }, [connectionMap]);

  // Keep ref updated with latest fetchNotifications
  useEffect(() => {
    fetchNotificationsRef.current = fetchNotifications;
  }, [fetchNotifications]);

  // Fetch notifications on mount and periodically
  useEffect(() => {
    fetchNotifications();
    const interval = setInterval(fetchNotifications, 15000);
    return () => clearInterval(interval);
  }, [clientId, fetchNotifications]);

  // When activeNodeId changes from an external source (BranchView),
  // auto-scroll to the matching notification and treat as highlight
  useEffect(() => {
    if (!activeNodeId) return;
    // Only react if this wasn't triggered by our own hover/pin
    if (activeNodeId === hoveredItem || activeNodeId === pinnedItem) return;

    const el = itemRefs.current[activeNodeId];
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [activeNodeId]); // intentionally exclude hoveredItem/pinnedItem to avoid loops

  // Format relative time
  const formatTime = (timestamp) => {
    if (!timestamp) return '';
    const now = new Date();
    const time = new Date(timestamp);
    const diffMs = now - time;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return time.toLocaleDateString('en-US');
  };

  // Scroll to post in feed
  const scrollToPost = (postId) => {
    const element = document.getElementById(`post-${postId}`);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      element.classList.add('ring-2', 'ring-blue-500', 'ring-opacity-50');
      setTimeout(() => {
        element.classList.remove('ring-2', 'ring-blue-500', 'ring-opacity-50');
      }, 2000);
    }
  };

  // Handle item click: toggle pin + scroll to post + notify BranchView
  const handleItemClick = (notif) => {
    const newPinned = pinnedItem === notif.id ? null : notif.id;
    setPinnedItem(newPinned);
    if (onNodeActive) onNodeActive(newPinned);
    scrollToPost(notif.postId);
  };

  const handleItemHover = (notifId) => {
    setHoveredItem(notifId);
    if (!pinnedItem && onNodeActive) onNodeActive(notifId);
  };

  const handleItemLeave = () => {
    setHoveredItem(null);
    if (!pinnedItem && onNodeActive) onNodeActive(null);
  };

  // Truncate content
  const truncate = (text, maxLength = 80) => {
    if (!text) return '';
    return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
  };

  if (loading && notifications.length === 0) {
    return (
      <div className="p-8 text-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mx-auto mb-3"></div>
        <p className="text-sm text-gray-500">Loading activity...</p>
      </div>
    );
  }

  if (notifications.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-gray-400 h-full">
        <div className="w-16 h-16 bg-gray-50 rounded-full flex items-center justify-center mb-4">
          <Activity className="w-8 h-8 text-gray-300" />
        </div>
        <p className="text-gray-500 font-medium">No recent activity</p>
        <p className="text-xs text-gray-400 mt-1">Agents will start analysis when you create a post</p>
      </div>
    );
  }

  // Toggle tag filter
  const toggleTagFilter = (tag) => {
    setSelectedTags(prev =>
      prev.includes(tag)
        ? prev.filter(t => t !== tag)
        : [...prev, tag]
    );
  };

  // Filter notifications by selected tags and saved status
  const filteredNotifications = notifications.filter(n => {
    // Apply saved filter
    if (showSavedOnly && !n.isSaved) return false;

    // Apply tag filter
    if (selectedTags.length > 0) {
      if (!n.tags || !n.tags.some(t => selectedTags.includes(t))) return false;
    }

    return true;
  });

  // Unpin when clicking empty space in the container
  const handleContainerClick = (e) => {
    if (e.target === e.currentTarget) {
      setPinnedItem(null);
      if (onNodeActive) onNodeActive(null);
    }
  };

  return (
    <div className="space-y-3" onClick={handleContainerClick}>
      {/* Tag Filter Bar */}
      <div className="flex flex-wrap gap-1.5 pb-3 border-b border-gray-100">
        <span className="text-xs text-gray-500 mr-1 self-center">Filter:</span>
        {/* Saved Filter */}
        <button
          onClick={() => setShowSavedOnly(!showSavedOnly)}
          className={`text-[10px] font-medium px-2 py-1 rounded-full border transition-all
            ${showSavedOnly
              ? 'bg-yellow-100 text-yellow-700 border-yellow-200 ring-2 ring-offset-1 ring-yellow-400'
              : 'bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100'
            }`}
        >
          🔖 Saved
        </button>
        <span className="text-gray-300 self-center">|</span>
        {/* Tag Filters */}
        {Object.entries(TAG_CONFIG).map(([tag, config]) => (
          <button
            key={tag}
            onClick={() => toggleTagFilter(tag)}
            className={`text-[10px] font-medium px-2 py-1 rounded-full border transition-all
              ${selectedTags.includes(tag)
                ? `${config.color} ring-2 ring-offset-1 ring-blue-400`
                : 'bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100'
              }`}
          >
            {config.label}
          </button>
        ))}
        {(selectedTags.length > 0 || showSavedOnly) && (
          <button
            onClick={() => { setSelectedTags([]); setShowSavedOnly(false); }}
            className="text-[10px] text-gray-400 hover:text-gray-600 ml-1"
          >
            Clear
          </button>
        )}
      </div>

      {/* Connection Legend */}
      <div className="flex flex-wrap items-center gap-3 py-2 px-2 bg-gray-50 rounded-lg text-xs">
        <span className="text-gray-500 font-medium">Links:</span>
        <span className="flex items-center gap-1 text-green-600 font-medium"><Check className="w-3.5 h-3.5" /> supports</span>
        <span className="flex items-center gap-1 text-red-600 font-medium"><X className="w-3.5 h-3.5" /> contradicts</span>
        <span className="flex items-center gap-1 text-blue-600 font-medium"><ArrowRight className="w-3.5 h-3.5" /> answers</span>
        <span className="flex items-center gap-1 text-purple-600 font-medium"><Plus className="w-3.5 h-3.5" /> extends</span>
        <span className="flex items-center gap-1 text-orange-600 font-medium"><HelpCircle className="w-3.5 h-3.5" /> questions</span>
      </div>

      {/* Filtered notifications count */}
      {(selectedTags.length > 0 || showSavedOnly) && (
        <p className="text-xs text-gray-400">
          Showing {filteredNotifications.length} of {notifications.length} activities
          {showSavedOnly && ' (Saved only)'}
        </p>
      )}

      {filteredNotifications.map((notif, index) => {
        // Get agent icon by role first, then by name
        const roleIcon = notif.authorRole ? AGENT_ICONS[notif.authorRole] : null;
        const agentInfo = roleIcon || agentIcons[notif.agent] || { icon: Activity, color: 'bg-gray-100 text-gray-600', border: 'border-gray-200' };
        const AgentIcon = agentInfo.icon;

        // Check if unread based on timestamp
        const notifTime = new Date(notif.timestamp).getTime();
        const isUnread = notifTime > lastReadTime;
        
        // Active item: pin takes priority, then hover, then external activeNodeId
        const activeItem = pinnedItem || hoveredItem || activeNodeId;
        
        // State checks
        const isHovered = hoveredItem === notif.id;
        const isPinned = pinnedItem === notif.id;
        const isExternalActive = activeNodeId === notif.id && !isHovered && !isPinned;
        const isActiveItem = activeItem === notif.id;
        const activeNotif = filteredNotifications.find(n => n.id === activeItem);
        const isSamePostGroup = activeNotif && activeNotif.postId === notif.postId && activeItem !== notif.id;
        
        // Semantic connection checks
        const connectedIds = activeItem ? getConnectedIds(activeItem) : new Set();
        const isSemanticConnected = connectedIds.has(notif.id);
        
        // Get connection type if this item is connected to active item
        const itemConnections = connectionMap.get(notif.id) || [];
        const connectionToActive = activeItem ? itemConnections.find(c => c.targetId === activeItem) : null;
        const activeConnections = connectionMap.get(activeItem) || [];
        const connectionFromActive = activeConnections.find(c => c.targetId === notif.id);
        const activeConnection = connectionToActive || connectionFromActive;
        
        // Check structural position (for same-post group highlighting)
        const prevNotif = index > 0 ? filteredNotifications[index - 1] : null;
        const nextNotif = index < filteredNotifications.length - 1 ? filteredNotifications[index + 1] : null;
        void prevNotif; void nextNotif; // used for potential future extensions

        return (
          <div
            key={notif.id}
            ref={el => { itemRefs.current[notif.id] = el; }}
            onClick={() => handleItemClick(notif)}
            onMouseEnter={() => handleItemHover(notif.id)}
            onMouseLeave={handleItemLeave}
            className={`group relative py-3 bg-white rounded-lg border-2 transition-all duration-200 cursor-pointer
              ${isHovered ? 'border-blue-500 shadow-md z-10' : ''}
              ${isPinned && !isHovered ? 'border-blue-500 bg-blue-50/40 shadow-md z-10 ring-1 ring-blue-300' : ''}
              ${isExternalActive ? 'border-blue-400 bg-blue-50/60 shadow-md z-10 ring-1 ring-blue-200' : ''}
              ${isSamePostGroup && !isActiveItem ? 'border-indigo-400 bg-indigo-50/80 shadow-md' : ''}
              ${isSemanticConnected && !isActiveItem && !isSamePostGroup ? 'border-purple-500 bg-purple-50/80 shadow-md' : ''}
              ${!isActiveItem && !isSamePostGroup && !isSemanticConnected && !isExternalActive ? (isUnread ? 'border-blue-200 bg-blue-50/30' : 'border-gray-100 hover:border-gray-300 hover:shadow-sm') : ''}
            `}
          >
            {isPinned && (
              <span className="absolute top-1.5 right-1.5 z-20">
                <Pin className="w-3.5 h-3.5 text-blue-500 fill-blue-500" />
              </span>
            )}
            {isUnread && !isPinned && (
              <span className="absolute top-2 right-2 w-2 h-2 bg-red-500 rounded-full animate-pulse"></span>
            )}

            <div className="flex items-stretch">
              {/* Left: Structure indicator */}
              <div className="flex-shrink-0 w-7 flex flex-col items-center justify-center relative">
                {/* P or arrow badge */}
                <div className={`flex items-center justify-center rounded transition-all
                  ${notif.type === 'proactive_post'
                    ? (isSamePostGroup || isActiveItem
                        ? 'w-6 h-5 bg-indigo-500 text-white shadow-md'
                        : 'w-6 h-5 bg-gray-100 text-gray-500')
                    : (isSamePostGroup || isActiveItem
                        ? 'w-5 h-5 bg-indigo-500 text-white shadow-md'
                        : 'w-4 h-4 bg-gray-100 text-gray-400')
                  }`}
                >
                  <span className={`font-bold ${isSamePostGroup || isActiveItem ? 'text-[10px]' : 'text-[9px]'}`}>
                    {notif.type === 'proactive_post' ? `#${notif.postId}` : '↳'}
                  </span>
                </div>
              </div>

              {/* Center: Main content */}
              <div className="flex-1 min-w-0 px-2">
                <div className="flex items-center gap-1.5 mb-1">
                  {/* Agent Icon (smaller) */}
                  <div className={`flex-shrink-0 w-6 h-6 rounded-full ${agentInfo.color} flex items-center justify-center`}>
                    <AgentIcon className="w-3 h-3" />
                  </div>
                  <p className="text-[11px] font-semibold text-gray-700 truncate flex-1">
                    {notif.agent}
                  </p>
                  <span className="text-[9px] text-gray-400 flex-shrink-0">
                    {formatTime(notif.timestamp)}
                  </span>
                </div>

                {/* Tags */}
                <div className="flex items-center gap-1 mb-1 flex-wrap">
                  {notif.tags && notif.tags.length > 0 ? (
                    notif.tags.slice(0, 2).map(tag => {
                      const config = TAG_CONFIG[tag]
                      if (!config) return null
                      return (
                        <span key={tag} className={`text-[9px] font-medium px-1 py-0.5 rounded ${config.color}`}>
                          {config.label}
                        </span>
                      )
                    })
                  ) : (
                    <span className="text-[9px] text-gray-400">
                      {notif.type === 'proactive_post' ? 'Post' : 'Reply'}
                    </span>
                  )}
                </div>

                {/* Content */}
                <p className="text-xs text-gray-600 line-clamp-2 leading-relaxed">
                  {truncate(notif.type === 'reply' ? notif.replyContent : notif.content, 100)}
                </p>
              </div>

              {/* Right: Semantic connection indicator (only on hover) */}
              <div className="flex-shrink-0 w-6 flex flex-col items-center justify-center">
                {activeConnection && (
                  <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold
                    ${CONNECTION_CONFIG[activeConnection.relation]?.bg || 'bg-gray-100'}
                    ${CONNECTION_CONFIG[activeConnection.relation]?.color || 'text-gray-600'}`}
                    title={activeConnection.relation}
                  >
                    {CONNECTION_CONFIG[activeConnection.relation]?.label || '•'}
                  </div>
                )}
              </div>
            </div>

            {/* Hover Arrow */}
            <div className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity text-gray-300">
              <ChevronRight className="w-4 h-4" />
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default NotificationTab;
