import React, { useState, useEffect, useRef, useCallback, useMemo, memo } from 'react';
import { Database, ZoomIn, ZoomOut, Maximize2, AlertCircle, ChevronDown } from 'lucide-react';
import axios from 'axios';

// Simple 3-color scheme: root, post, reply
const NODE_COLORS = {
  dataset: { fill: '#f59e0b', stroke: '#d97706' },  // amber
  post: { fill: '#3b82f6', stroke: '#2563eb' },   // blue
  reply: { fill: '#9ca3af', stroke: '#6b7280' },   // gray
};

// Agent role highlight colors (consistent with Activity Tab)
const AGENT_HIGHLIGHT = {
  statistics: { color: '#3b82f6', bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-300', activeBg: 'bg-blue-100', label: 'Statistical Analyst' },
  visualization: { color: '#22c55e', bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-300', activeBg: 'bg-green-100', label: 'Visualization Expert' },
  insight: { color: '#a855f7', bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-300', activeBg: 'bg-purple-100', label: 'Intelligence' },
  scanner: { color: '#f97316', bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-300', activeBg: 'bg-orange-100', label: 'Data Scout' },
  summary: { color: '#eab308', bg: 'bg-yellow-50', text: 'text-yellow-700', border: 'border-yellow-300', activeBg: 'bg-yellow-100', label: 'Summary' },
  user: { color: '#6b7280', bg: 'bg-gray-50', text: 'text-gray-700', border: 'border-gray-300', activeBg: 'bg-gray-200', label: 'User' },
};

// Semantic connection colors
const RELATION_COLORS = {
  supports: { stroke: '#22c55e', label: 'supports', icon: '✓' },
  contradicts: { stroke: '#ef4444', label: 'contradicts', icon: '✗' },
  answers: { stroke: '#3b82f6', label: 'answers', icon: '→' },
  extends: { stroke: '#a855f7', label: 'extends', icon: '+' },
  questions: { stroke: '#f97316', label: 'questions', icon: '?' },
};

const UI_TEXT = {
  English: {
    loading: 'Loading branch view...',
    loadError: 'Failed to load branch data',
    retry: 'Retry',
    noData: 'No data yet',
    noDataSub: 'Upload a dataset to see the branch view',
    branchView: 'Branch View',
    branchSubtitle: 'Conversation flow and branching structure',
    timeline: 'Timeline',
    perThread: 'Per-Thread',
    timelineTitle: 'Global chronological timeline',
    threadTitle: 'Group by post thread',
    zoomOut: 'Zoom out',
    zoomIn: 'Zoom in',
    resetView: 'Reset view',
    filterByAgent: 'Filter by agent',
    clearFilter: 'Clear filter',
    links: 'Links:',
    footerHelp: 'Click to pin • Double-click to navigate • Drag to pan • Scroll to zoom',
    dataset: 'Dataset',
    post: 'Post',
    reply: 'Reply',
    noContent: 'No content',
    time: 'time',
    threads: 'threads',
  },
  Korean: {
    loading: '브랜치 뷰를 불러오는 중...',
    loadError: '브랜치 데이터를 불러오지 못했습니다',
    retry: '다시 시도',
    noData: '아직 데이터가 없습니다',
    noDataSub: '데이터셋을 업로드하면 브랜치 뷰를 볼 수 있습니다',
    branchView: '브랜치 뷰',
    branchSubtitle: '대화 흐름과 분기 구조',
    timeline: '타임라인',
    perThread: '스레드별',
    timelineTitle: '전체 시간순 타임라인',
    threadTitle: '포스트 스레드별 그룹화',
    zoomOut: '축소',
    zoomIn: '확대',
    resetView: '보기 초기화',
    filterByAgent: '에이전트별 필터',
    clearFilter: '필터 지우기',
    links: '관계:',
    footerHelp: '클릭하여 고정 • 더블클릭하여 이동 • 드래그하여 이동 • 스크롤하여 확대/축소',
    dataset: '데이터셋',
    post: '포스트',
    reply: '댓글',
    noContent: '내용 없음',
    time: '시간',
    threads: '스레드',
  },
};

const branchViewCache = new Map();
const getWsMessageId = (message) => {
  if (!message) return null;
  return `${message.type}_${message.post_id || message.post?.id || ''}_${message.reply?.id || ''}`;
};

const BranchView = memo(({ clientId, wsMessage, postsVersion, onNavigateToPost, activeNodeId, onNodeActive }) => {
  const cachedState = clientId ? branchViewCache.get(clientId) : null;
  const incomingMessageId = getWsMessageId(wsMessage);
  const hasStaleCache = Boolean(
    cachedState &&
    incomingMessageId &&
    cachedState.lastMessageId &&
    cachedState.lastMessageId !== incomingMessageId
  );
  const [treeData, setTreeData] = useState(cachedState?.treeData || { root: null, nodes: [] });
  const [connectionMap, setConnectionMap] = useState(() => new Map(cachedState?.connectionEntries || []));
  const [loading, setLoading] = useState(!cachedState);
  const [error, setError] = useState(null);
  const [language, setLanguage] = useState(cachedState?.language || 'English');
  const [hoveredNode, setHoveredNode] = useState(null);
  const [pinnedNodeId, setPinnedNodeId] = useState(cachedState?.pinnedNodeId || null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [layoutMode, setLayoutMode] = useState(cachedState?.layoutMode || 'timeline'); // 'timeline' (global chrono) or 'thread' (per-post)
  const [selectedAgent, setSelectedAgent] = useState(cachedState?.selectedAgent || null); // Agent role filter
  const [filterOpen, setFilterOpen] = useState(false); // Dropdown open state
  const filterRef = useRef(null);

  // Pan and zoom state
  const [transform, setTransform] = useState(cachedState?.transform || { x: 80, y: 0, scale: 1.0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });

  const containerRef = useRef(null);
  const svgRef = useRef(null);
  const dataFetchedRef = useRef(false);

  // Fetch tree + timeline data
  const fetchTreeData = useCallback(async (silent = false) => {
    if (!clientId) { setLoading(false); return; }
    try {
      if (!silent) {
        setLoading(true);
      }
      setError(null);
      const [treeRes, timelineRes, settingsRes] = await Promise.all([
        axios.get('/api/tree', { params: { client_id: clientId } }),
        axios.get('/api/timeline', { params: { client_id: clientId } }).catch(() => ({ data: { items: [] } })),
        axios.get('/api/settings', { params: { client_id: clientId } }).catch(() => ({ data: { language: 'English' } })),
      ]);
      setTreeData(treeRes.data);
      const savedLanguage = settingsRes?.data?.language || 'English';
      if (savedLanguage === 'Korean') {
        setLanguage('Korean');
      } else {
        setLanguage('English');
      }

      // Build connectionMap from timeline data
      const connMap = new Map();
      (timelineRes.data.items || []).forEach(item => {
        if (item.connections?.length > 0) {
          connMap.set(item.id, item.connections.map(c => ({
            targetId: c.target_id || c.targetId,
            relation: c.relation,
            confidence: c.confidence,
          })));
        }
      });
      setConnectionMap(connMap);
    } catch (err) {
      console.error('Failed to fetch tree data:', err);
      if (!silent) {
        setError(UI_TEXT[language]?.loadError || UI_TEXT.English.loadError);
      }
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }, [clientId, language]);

  const t = UI_TEXT[language] || UI_TEXT.English;

  // Initial fetch
  useEffect(() => {
    if (!dataFetchedRef.current && clientId) {
      dataFetchedRef.current = true;
      fetchTreeData(Boolean(cachedState));

      // If the cache predates the latest websocket event, refresh again shortly after
      // to catch any delayed backend updates for the new post/reply.
      if (hasStaleCache) {
        const refreshTimer = setTimeout(() => {
          fetchTreeData(true);
        }, 1200);
        return () => clearTimeout(refreshTimer);
      }
    }
  }, [fetchTreeData, clientId, cachedState, hasStaleCache]);

  // Refresh when the feed state changes, including user-created posts that may not
  // arrive as a WebSocket new_post event in the same tab.
  const lastPostsVersionRef = useRef(postsVersion);
  useEffect(() => {
    if (!clientId || !postsVersion) return;
    if (postsVersion === lastPostsVersionRef.current) return;
    lastPostsVersionRef.current = postsVersion;
    if (!dataFetchedRef.current) return;

    const refreshTimer = setTimeout(() => {
      fetchTreeData(true);
    }, 250);
    const followUpTimer = setTimeout(() => {
      fetchTreeData(true);
    }, 1500);

    return () => {
      clearTimeout(refreshTimer);
      clearTimeout(followUpTimer);
    };
  }, [clientId, postsVersion, fetchTreeData]);

  // Refetch on new post/reply
  const lastMessageIdRef = useRef(null);
  useEffect(() => {
    if (!wsMessage) return;
    const messageId = getWsMessageId(wsMessage);
    if (messageId === lastMessageIdRef.current) return;
    if (wsMessage.type === 'new_post' || wsMessage.type === 'new_reply') {
      lastMessageIdRef.current = messageId;
      fetchTreeData(true);

      // Follow-up refresh to avoid showing stale cached topology if backend-side
      // post/reply enrichment lands slightly after the websocket event.
      const refreshTimer = setTimeout(() => {
        fetchTreeData(true);
      }, 1200);
      return () => clearTimeout(refreshTimer);
    }
  }, [wsMessage, fetchTreeData]);

  // Persist branch view state per client so revisiting the tab restores instantly.
  useEffect(() => {
    if (!clientId) return;

    branchViewCache.set(clientId, {
      treeData,
      connectionEntries: Array.from(connectionMap.entries()),
      language,
      pinnedNodeId,
      layoutMode,
      selectedAgent,
      transform,
      lastMessageId: incomingMessageId || cachedState?.lastMessageId || null,
      cachedAt: Date.now(),
    });
  }, [clientId, treeData, connectionMap, language, pinnedNodeId, layoutMode, selectedAgent, transform, incomingMessageId, cachedState]);

  // Build tree structure from flat nodes
  const tree = useMemo(() => {
    const { root, nodes } = treeData;
    if (!root) return null;
    const nodeMap = new Map();
    nodeMap.set(root.id, { ...root, children: [] });
    nodes.forEach(node => nodeMap.set(node.id, { ...node, children: [] }));
    nodes.forEach(node => {
      const parent = nodeMap.get(node.parentId);
      if (parent) parent.children.push(nodeMap.get(node.id));
    });
    return nodeMap.get(root.id);
  }, [treeData]);

  // ===== LANE ASSIGNMENT & ORDER-BASED POSITIONING =====
  const { laidOutNodes, connections, svgWidth, svgHeight, threadSeparators } = useMemo(() => {
    if (!tree) return { laidOutNodes: [], connections: [], svgWidth: 800, svgHeight: 300, threadSeparators: [] };

    const LANE_HEIGHT = 95;
    const NODE_SPACING = 85;
    const PADDING_LEFT = 70;
    const PADDING_TOP = 60;
    const PADDING_BOTTOM = 50;

    const positioned = [];
    const posMap = new Map();
    const finalSeparators = [];
    let totalLanes = 1;

    if (layoutMode === 'thread') {
      // ===== PER-THREAD MODE =====
      // Each post gets its own lane band. All posts connect to dataset.
      // Replies extend rightward within their post's band.

      // Collect ALL post-type nodes from the entire tree
      const allPostNodes = [];
      const collectPosts = (node) => {
        if (node.type === 'post') allPostNodes.push(node);
        if (node.children) node.children.forEach(c => collectPosts(c));
      };
      collectPosts(tree);
      allPostNodes.sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));

      // For each post, collect only its reply descendants (stop at child posts)
      const getThreadReplies = (postNode) => {
        const replies = [];
        const collect = (node) => {
          if (node.children) {
            const sorted = [...node.children].sort((a, b) =>
              new Date(a.created_at || 0) - new Date(b.created_at || 0)
            );
            sorted.forEach(c => {
              if (c.type !== 'post') {
                replies.push(c);
                collect(c);
              }
            });
          }
        };
        collect(postNode);
        return replies;
      };

      // Assign lane bands per thread
      let laneOffset = 0;
      const threadLaneInfo = [];

      allPostNodes.forEach(post => {
        const replies = getThreadReplies(post);
        const threadNodes = [post, ...replies];

        const threadLaneMap = new Map();
        let threadNextLane = 0;

        const assignThreadLanes = (node, lane) => {
          threadLaneMap.set(node.id, lane);
          const replyChildren = (node.children || [])
            .filter(c => c.type !== 'post')
            .sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));
          if (replyChildren.length > 0) {
            assignThreadLanes(replyChildren[0], lane);
            for (let i = 1; i < replyChildren.length; i++) {
              threadNextLane++;
              assignThreadLanes(replyChildren[i], threadNextLane);
            }
          }
        };

        assignThreadLanes(post, 0);
        const lanesNeeded = threadNextLane + 1;

        threadLaneInfo.push({
          post,
          threadNodes,
          baseLane: laneOffset,
          laneCount: lanesNeeded,
          laneMap: threadLaneMap,
        });

        laneOffset += lanesNeeded;
      });

      totalLanes = Math.max(laneOffset, 1);

      // Position dataset node (centered vertically)
      const datasetCx = PADDING_LEFT;
      const datasetMidY = totalLanes > 1
        ? PADDING_TOP + ((totalLanes - 1) * LANE_HEIGHT) / 2
        : PADDING_TOP;
      const datasetNode = { ...tree, lane: 0, cx: datasetCx, cy: datasetMidY };
      positioned.push(datasetNode);
      posMap.set(tree.id, datasetNode);

      // Position each thread
      let maxThreadWidth = 0;

      threadLaneInfo.forEach(({ threadNodes, baseLane, laneMap: threadLaneMap }) => {
        threadNodes.forEach((node, idx) => {
          const localLane = threadLaneMap.get(node.id) ?? 0;
          const globalLane = baseLane + localLane;
          const cx = PADDING_LEFT + (idx + 1) * NODE_SPACING;
          const cy = PADDING_TOP + globalLane * LANE_HEIGHT;
          const posNode = { ...node, lane: globalLane, cx, cy };
          positioned.push(posNode);
          posMap.set(node.id, posNode);
        });

        if (threadNodes.length + 1 > maxThreadWidth) {
          maxThreadWidth = threadNodes.length + 1;
        }
      });

      // Build connections — posts always connect to dataset, replies to their parent within the thread
      const conns = [];
      positioned.forEach(n => {
        if (n.type === 'post') {
          const dataset = posMap.get(tree.id);
          if (dataset) conns.push({ from: dataset, to: n });
        } else if (n.parentId) {
          const parent = posMap.get(n.parentId);
          if (parent) conns.push({ from: parent, to: n });
        }
      });

      const totalWidth = PADDING_LEFT + maxThreadWidth * NODE_SPACING + 60;
      const totalHeight = PADDING_TOP + totalLanes * LANE_HEIGHT + PADDING_BOTTOM;

      return {
        laidOutNodes: positioned,
        connections: conns,
        svgWidth: totalWidth,
        svgHeight: Math.max(totalHeight, 250),
        threadSeparators: finalSeparators,
      };

    } else {
      // ===== GLOBAL TIMELINE MODE =====
      // Each post gets its own lane band. Replies sub-lane within their post's band.
      // Connection lines follow the actual parentId (derived posts connect to origin).
      // X-positioning is strictly chronological across all nodes.

      // Flatten all nodes
      const allNodes = [];
      const flattenTree = (node) => {
        allNodes.push(node);
        if (node.children) node.children.forEach(c => flattenTree(c));
      };
      flattenTree(tree);

      // Collect all post nodes sorted by creation time
      const allPostNodes = allNodes
        .filter(n => n.type === 'post')
        .sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));

      // Assign each post its own lane band with reply sub-lanes
      const laneMap = new Map();
      laneMap.set(tree.id, 0); // dataset on lane 0
      let laneOffset = 0;

      allPostNodes.forEach(post => {
        // DFS within this post's replies only (skip child posts)
        let threadNextLane = 0;

        const assignReplyLanes = (node, lane) => {
          laneMap.set(node.id, laneOffset + lane);
          const replyChildren = (node.children || [])
            .filter(c => c.type !== 'post')
            .sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));
          if (replyChildren.length > 0) {
            assignReplyLanes(replyChildren[0], lane);
            for (let i = 1; i < replyChildren.length; i++) {
              threadNextLane++;
              assignReplyLanes(replyChildren[i], threadNextLane);
            }
          }
        };

        assignReplyLanes(post, 0);
        laneOffset += threadNextLane + 1;
      });

      totalLanes = Math.max(laneOffset, 1);

      // Sort all nodes chronologically for X-positioning
      const rootNode = allNodes.find(n => n.type === 'dataset');
      const nodesWithTime = allNodes.filter(n => n.created_at);
      nodesWithTime.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));

      const orderedNodes = [];
      if (rootNode) orderedNodes.push(rootNode);
      nodesWithTime.forEach(n => {
        if (n.id !== rootNode?.id) orderedNodes.push(n);
      });

      if (nodesWithTime.length === 0) {
        allNodes.forEach(n => {
          if (!orderedNodes.includes(n)) orderedNodes.push(n);
        });
      }

      orderedNodes.forEach((node, idx) => {
        const lane = laneMap.get(node.id) ?? 0;
        const cx = PADDING_LEFT + idx * NODE_SPACING;
        const cy = PADDING_TOP + lane * LANE_HEIGHT;
        const posNode = { ...node, lane, cx, cy };
        positioned.push(posNode);
        posMap.set(node.id, posNode);
      });

      // Connections follow actual parentId
      const conns = [];
      positioned.forEach(n => {
        if (n.parentId) {
          const parent = posMap.get(n.parentId);
          if (parent) conns.push({ from: parent, to: n });
        }
      });

      const totalWidth = PADDING_LEFT + orderedNodes.length * NODE_SPACING + 60;
      const totalHeight = PADDING_TOP + totalLanes * LANE_HEIGHT + PADDING_BOTTOM;

      return {
        laidOutNodes: positioned,
        connections: conns,
        svgWidth: totalWidth,
        svgHeight: Math.max(totalHeight, 250),
        threadSeparators: [],
      };
    }
  }, [tree, layoutMode]);

  // Resolve the agent role for a node
  const getAgentRole = useCallback((node) => {
    if (node.type === 'dataset') return null;
    if (node.authorType === 'user') return 'user';
    return node.authorRole || null;
  }, []);

  // Collect unique agent roles present in the tree for filter buttons
  const availableRoles = useMemo(() => {
    const roles = new Set();
    laidOutNodes.forEach(node => {
      const role = node.type === 'dataset' ? null : (node.authorType === 'user' ? 'user' : node.authorRole);
      if (role) roles.add(role);
    });
    const order = ['user', 'statistics', 'visualization', 'insight', 'scanner', 'summary'];
    return order.filter(r => roles.has(r));
  }, [laidOutNodes]);

  // Check if a node matches the selected agent filter
  const isNodeHighlighted = useCallback((node) => {
    if (!selectedAgent) return true;
    return getAgentRole(node) === selectedAgent;
  }, [selectedAgent, getAgentRole]);

  // Get all semantic connections for the active node (bidirectional)
  const semanticArcs = useMemo(() => {
    const effectiveId = pinnedNodeId || activeNodeId || hoveredNode?.id;
    if (!effectiveId || connectionMap.size === 0) return [];

    // Build positioned node lookup
    const posMap = new Map();
    laidOutNodes.forEach(n => posMap.set(n.id, n));

    const arcs = [];
    const seen = new Set();

    // Outgoing connections from active node
    const outgoing = connectionMap.get(effectiveId) || [];
    outgoing.forEach(conn => {
      const from = posMap.get(effectiveId);
      const to = posMap.get(conn.targetId);
      if (from && to) {
        const key = `${effectiveId}-${conn.targetId}`;
        if (!seen.has(key)) {
          seen.add(key);
          arcs.push({ from, to, relation: conn.relation });
        }
      }
    });

    // Incoming connections to active node
    connectionMap.forEach((connections, sourceId) => {
      connections.forEach(conn => {
        if (conn.targetId === effectiveId) {
          const from = posMap.get(sourceId);
          const to = posMap.get(effectiveId);
          if (from && to) {
            const key = `${sourceId}-${effectiveId}`;
            if (!seen.has(key)) {
              seen.add(key);
              arcs.push({ from, to, relation: conn.relation });
            }
          }
        }
      });
    });

    return arcs;
  }, [pinnedNodeId, activeNodeId, hoveredNode, connectionMap, laidOutNodes]);

  // Build a quadratic bezier arc between two nodes (curved above)
  // Endpoints are offset to the edge of each node circle so the arrowhead is visible
  const buildArcPath = useCallback((from, to, fromRadius, toRadius) => {
    const dx = to.cx - from.cx;
    const dy = to.cy - from.cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist === 0) return { path: '', labelX: from.cx, labelY: from.cy };

    const curveHeight = Math.min(dist * 0.4, 80);
    const midX = (from.cx + to.cx) / 2;
    const midY = Math.min(from.cy, to.cy) - curveHeight;

    // Direction from control point to target for proper tangent at endpoint
    const tdx = to.cx - midX;
    const tdy = to.cy - midY;
    const tLen = Math.sqrt(tdx * tdx + tdy * tdy) || 1;
    const endX = to.cx - (tdx / tLen) * (toRadius + 6);
    const endY = to.cy - (tdy / tLen) * (toRadius + 6);

    // Direction from control point to source
    const sdx = from.cx - midX;
    const sdy = from.cy - midY;
    const sLen = Math.sqrt(sdx * sdx + sdy * sdy) || 1;
    const startX = from.cx - (sdx / sLen) * (fromRadius + 2);
    const startY = from.cy - (sdy / sLen) * (fromRadius + 2);

    return {
      path: `M ${startX} ${startY} Q ${midX} ${midY}, ${endX} ${endY}`,
      labelX: midX,
      labelY: midY,
    };
  }, []);

  // Get color for a node (simple: root / post / reply)
  const getNodeColor = useCallback((node) => {
    if (node.type === 'dataset') return NODE_COLORS.dataset;
    if (node.type === 'post') return NODE_COLORS.post;
    return NODE_COLORS.reply;
  }, []);

  // Get node radius
  const getNodeRadius = useCallback((node) => {
    if (node.type === 'dataset') return 22;
    if (node.type === 'post') return 20;
    return 16;
  }, []);

  // Build connection path
  const buildConnectionPath = useCallback((from, to) => {
    const fromRadius = getNodeRadius(from);
    const toRadius = getNodeRadius(to);
    if (from.cy === to.cy) {
      // Same lane: straight line from right edge of source to left edge of target
      return `M ${from.cx + fromRadius} ${from.cy} L ${to.cx - toRadius} ${to.cy}`;
    }
    // Different lane: S-curve bezier arriving at target node's left center
    const startX = from.cx + fromRadius;
    const startY = from.cy;
    const endX = to.cx - toRadius;
    const endY = to.cy;
    const cpX = (startX + endX) / 2;
    return `M ${startX} ${startY} C ${cpX} ${startY}, ${cpX} ${endY}, ${endX} ${endY}`;
  }, [getNodeRadius]);

  // Mouse handlers for panning
  const handleMouseDown = useCallback((e) => {
    if (e.target === svgRef.current || e.target.tagName === 'svg' || e.target.classList?.contains('pan-area')) {
      setIsDragging(true);
      setDragStart({ x: e.clientX - transform.x, y: e.clientY - transform.y });
    }
  }, [transform.x, transform.y]);

  const handleMouseMove = useCallback((e) => {
    setMousePos({ x: e.clientX, y: e.clientY });
    if (isDragging) {
      setTransform(prev => ({ ...prev, x: e.clientX - dragStart.x, y: e.clientY - dragStart.y }));
    }
  }, [isDragging, dragStart.x, dragStart.y]);

  const handleMouseUp = useCallback((e) => {
    if (isDragging) {
      setIsDragging(false);
      return;
    }
    // Click on empty space: unpin
    if (e.target === svgRef.current || e.target.tagName === 'svg' || e.target.classList?.contains('pan-area')) {
      if (pinnedNodeId) {
        setPinnedNodeId(null);
        if (onNodeActive) onNodeActive(null);
      }
    }
  }, [isDragging, pinnedNodeId, onNodeActive]);

  const handleWheel = useCallback((e) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.1 : 0.1;
    setTransform(prev => ({ ...prev, scale: Math.max(0.3, Math.min(3, prev.scale + delta)) }));
  }, []);

  const zoomIn = useCallback(() => setTransform(prev => ({ ...prev, scale: Math.min(3, prev.scale + 0.2) })), []);
  const zoomOut = useCallback(() => setTransform(prev => ({ ...prev, scale: Math.max(0.3, prev.scale - 0.2) })), []);
  const resetView = useCallback(() => setTransform({ x: 80, y: 0, scale: 1.0 }), []);

  // Single click: toggle pin
  const handleNodeClick = useCallback((node) => {
    setPinnedNodeId(prev => {
      const newId = prev === node.id ? null : node.id;
      if (onNodeActive) onNodeActive(newId);
      return newId;
    });
  }, [onNodeActive]);

  // Double click: navigate to post in feed
  const handleNodeDoubleClick = useCallback((node) => {
    if (node.postId && onNavigateToPost) onNavigateToPost(node.postId);
  }, [onNavigateToPost]);

  const handleNodeHover = useCallback((node) => {
    setHoveredNode(node);
    if (!pinnedNodeId && onNodeActive) onNodeActive(node.id);
  }, [onNodeActive, pinnedNodeId]);

  const handleNodeLeave = useCallback(() => {
    setHoveredNode(null);
    if (!pinnedNodeId && onNodeActive) onNodeActive(null);
  }, [onNodeActive, pinnedNodeId]);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (filterRef.current && !filterRef.current.contains(e.target)) {
        setFilterOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Format time for tooltip
  const formatTime = useCallback((dateStr) => {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return d.toLocaleString(language === 'Korean' ? 'ko-KR' : 'en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }, [language]);

  // --- Render states ---
  if (loading) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center bg-gray-50 text-gray-400">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mb-4" />
        <p className="text-lg">{t.loading}</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center bg-gray-50 text-red-400">
        <AlertCircle className="w-12 h-12 mb-4" />
        <p className="text-lg">{error}</p>
        <button onClick={fetchTreeData} className="mt-4 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600">{t.retry}</button>
      </div>
    );
  }

  if (!tree) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center bg-gray-50 text-gray-400">
        <Database className="w-16 h-16 mb-4 opacity-50" />
        <p className="text-xl font-medium">{t.noData}</p>
        <p className="text-sm mt-2">{t.noDataSub}</p>
      </div>
    );
  }

  const AXIS_Y = svgHeight - 20;

  return (
    <div className="flex-1 flex flex-col bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-gray-800">{t.branchView}</h2>
          <p className="text-sm text-gray-500">{t.branchSubtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          {/* Layout mode toggle */}
          <div className="flex items-center bg-gray-100 rounded-lg p-0.5 mr-2">
            <button
              onClick={() => setLayoutMode('timeline')}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${layoutMode === 'timeline'
                ? 'bg-white text-gray-800 shadow-sm'
                : 'text-gray-500 hover:text-gray-700'
                }`}
              title={t.timelineTitle}
            >
              {t.timeline}
            </button>
            <button
              onClick={() => setLayoutMode('thread')}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${layoutMode === 'thread'
                ? 'bg-white text-gray-800 shadow-sm'
                : 'text-gray-500 hover:text-gray-700'
                }`}
              title={t.threadTitle}
            >
              {t.perThread}
            </button>
          </div>

          <button onClick={zoomOut} className="p-2 hover:bg-gray-100 rounded-lg" title={t.zoomOut}>
            <ZoomOut className="w-5 h-5 text-gray-600" />
          </button>
          <span className="text-sm text-gray-500 w-14 text-center font-medium">{Math.round(transform.scale * 100)}%</span>
          <button onClick={zoomIn} className="p-2 hover:bg-gray-100 rounded-lg" title={t.zoomIn}>
            <ZoomIn className="w-5 h-5 text-gray-600" />
          </button>
          <button onClick={resetView} className="p-2 hover:bg-gray-100 rounded-lg ml-2" title={t.resetView}>
            <Maximize2 className="w-5 h-5 text-gray-600" />
          </button>
        </div>
      </div>

      {/* Legend + Agent Filter */}
      <div className="flex items-center gap-4 px-6 py-2 bg-white border-b border-gray-100 text-xs">
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full" style={{ backgroundColor: NODE_COLORS.dataset.fill }} /> {t.dataset}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full" style={{ backgroundColor: NODE_COLORS.post.fill }} /> {t.post}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full" style={{ backgroundColor: NODE_COLORS.reply.fill }} /> {t.reply}
        </span>

        {availableRoles.length > 0 && (
          <div className="relative" ref={filterRef}>
            <button
              onClick={() => setFilterOpen(prev => !prev)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border transition-all text-xs
                ${selectedAgent
                  ? `${AGENT_HIGHLIGHT[selectedAgent]?.activeBg} ${AGENT_HIGHLIGHT[selectedAgent]?.text} ${AGENT_HIGHLIGHT[selectedAgent]?.border}`
                  : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                }`}
            >
              {selectedAgent && (
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: AGENT_HIGHLIGHT[selectedAgent]?.color }} />
              )}
              {selectedAgent ? AGENT_HIGHLIGHT[selectedAgent]?.label : t.filterByAgent}
              <ChevronDown className={`w-3.5 h-3.5 transition-transform ${filterOpen ? 'rotate-180' : ''}`} />
            </button>

            {filterOpen && (
              <div className="absolute top-full left-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-50 py-1 min-w-[180px]">
                {selectedAgent && (
                  <button
                    onClick={() => { setSelectedAgent(null); setFilterOpen(false); }}
                    className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-gray-400 hover:bg-gray-50 transition-colors"
                  >
                    {t.clearFilter}
                  </button>
                )}
                {availableRoles.map(role => {
                  const config = AGENT_HIGHLIGHT[role];
                  if (!config) return null;
                  const isActive = selectedAgent === role;
                  return (
                    <button
                      key={role}
                      onClick={() => { setSelectedAgent(isActive ? null : role); setFilterOpen(false); }}
                      className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors
                        ${isActive ? `${config.activeBg} ${config.text} font-medium` : 'text-gray-700 hover:bg-gray-50'}`}
                    >
                      <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: config.color }} />
                      {config.label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {semanticArcs.length > 0 && (
          <>
            <span className="text-gray-300 mx-1">|</span>
            <span className="text-gray-500 font-medium">{t.links}</span>
            {Object.entries(RELATION_COLORS).map(([key, val]) => (
              <span key={key} className="flex items-center gap-0.5" style={{ color: val.stroke }}>
                <span className="font-bold text-[10px]">{val.icon}</span>
                <span className="text-[10px]">{val.label}</span>
              </span>
            ))}
          </>
        )}

        <span className="ml-auto text-gray-400">{t.footerHelp}</span>
      </div>

      {/* SVG Canvas */}
      <div
        ref={containerRef}
        className="flex-1 overflow-hidden cursor-grab active:cursor-grabbing"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
      >
        <svg
          ref={svgRef}
          className="w-full h-full pan-area"
          style={{ minWidth: '100%', minHeight: '100%' }}
        >
          <defs>
            {Object.entries(RELATION_COLORS).map(([key, val]) => (
              <marker
                key={`arrow-${key}`}
                id={`arrow-${key}`}
                viewBox="0 0 10 6"
                refX="9"
                refY="3"
                markerWidth="7"
                markerHeight="5"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 3 L 0 6 Z" fill={val.stroke} />
              </marker>
            ))}
          </defs>
          <g transform={`translate(${transform.x}, ${transform.y}) scale(${transform.scale})`}>
            {/* Time axis line */}
            <line
              x1={30}
              y1={AXIS_Y}
              x2={svgWidth - 30}
              y2={AXIS_Y}
              stroke="#d1d5db"
              strokeWidth={1}
            />
            {/* Arrow at end of time axis */}
            <polygon
              points={`${svgWidth - 30},${AXIS_Y} ${svgWidth - 38},${AXIS_Y - 4} ${svgWidth - 38},${AXIS_Y + 4}`}
              fill="#9ca3af"
            />
            {/* Axis labels */}
            <text x={30} y={AXIS_Y + 18} fontSize="13" fill="#6b7280" fontFamily="sans-serif" fontWeight="500">
              {layoutMode === 'thread' ? 'T₁' : 't₀'}
            </text>
            <text x={svgWidth - 42} y={AXIS_Y + 18} fontSize="13" fill="#6b7280" fontFamily="sans-serif" fontWeight="500">
              {layoutMode === 'thread' ? 'Tₙ' : 'tₙ'}
            </text>
            <text
              x={(svgWidth) / 2}
              y={AXIS_Y + 18}
              fontSize="13"
              fill="#6b7280"
              fontFamily="sans-serif"
              fontWeight="500"
              textAnchor="middle"
            >
              {layoutMode === 'thread' ? t.threads : t.time}
            </text>

            {/* Connection lines */}
            {connections.map((conn, idx) => (
              <path
                key={`conn-${idx}`}
                d={buildConnectionPath(conn.from, conn.to)}
                fill="none"
                stroke={conn.from.cy === conn.to.cy ? '#6b7280' : '#a855f7'}
                strokeWidth={2.5}
                strokeDasharray={conn.from.cy !== conn.to.cy ? '6 3' : undefined}
                opacity={0.7}
              />
            ))}

            {/* Semantic connection arcs */}
            {semanticArcs.map((arc, idx) => {
              const rel = RELATION_COLORS[arc.relation] || RELATION_COLORS.extends;
              const markerId = `arrow-${arc.relation || 'extends'}`;
              const fromRadius = getNodeRadius(arc.from);
              const toRadius = getNodeRadius(arc.to);
              const { path, labelX, labelY } = buildArcPath(arc.from, arc.to, fromRadius, toRadius);
              return (
                <g key={`arc-${idx}`}>
                  <path
                    d={path}
                    fill="none"
                    stroke={rel.stroke}
                    strokeWidth={2.5}
                    strokeDasharray="6 3"
                    opacity={0.65}
                    markerEnd={`url(#${markerId})`}
                  />
                  <circle cx={labelX} cy={labelY} r={11} fill="white" stroke={rel.stroke} strokeWidth={2} />
                  <text
                    x={labelX}
                    y={labelY + 1}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fontSize="12"
                    fontWeight="800"
                    fill={rel.stroke}
                  >
                    {rel.icon}
                  </text>
                </g>
              );
            })}

            {/* Nodes */}
            {laidOutNodes.map((node, idx) => {
              const color = getNodeColor(node);
              const radius = getNodeRadius(node);
              const isHovered = hoveredNode?.id === node.id;
              const isPinned = pinnedNodeId === node.id;
              const isExternalActive = activeNodeId === node.id && !isHovered && !isPinned;
              const isActive = isHovered || isPinned || isExternalActive;
              const isLast = idx === laidOutNodes.length - 1;
              const highlighted = isNodeHighlighted(node);
              const agentRole = getAgentRole(node);
              const highlightColor = agentRole && AGENT_HIGHLIGHT[agentRole]?.color;

              // Check if this node is semantically connected to the active node
              const effectiveActive = pinnedNodeId || activeNodeId || hoveredNode?.id;
              const isSemanticTarget = effectiveActive && effectiveActive !== node.id &&
                semanticArcs.some(a => a.from.id === node.id || a.to.id === node.id);

              return (
                <g
                  key={`node-${node.id}`}
                  onClick={() => handleNodeClick(node)}
                  onDoubleClick={() => handleNodeDoubleClick(node)}
                  onMouseEnter={() => handleNodeHover(node)}
                  onMouseLeave={handleNodeLeave}
                  className="cursor-pointer"
                >
                  {/* Agent highlight ring (only on matching nodes when filter is active) */}
                  {selectedAgent && highlighted && highlightColor && (
                    <circle
                      cx={node.cx}
                      cy={node.cy}
                      r={radius + 5}
                      fill="none"
                      stroke={highlightColor}
                      strokeWidth={2.5}
                      opacity={0.8}
                    />
                  )}

                  {/* Pinned node ring */}
                  {isPinned && (
                    <circle
                      cx={node.cx}
                      cy={node.cy}
                      r={radius + 7}
                      fill="none"
                      stroke="#3b82f6"
                      strokeWidth={3}
                      opacity={0.8}
                    />
                  )}

                  {/* External active node ring (synced from Activity Tab) */}
                  {isExternalActive && (
                    <circle
                      cx={node.cx}
                      cy={node.cy}
                      r={radius + 7}
                      fill="none"
                      stroke="#3b82f6"
                      strokeWidth={3}
                      opacity={0.7}
                    >
                      <animate attributeName="opacity" values="0.7;0.3;0.7" dur="1.5s" repeatCount="indefinite" />
                    </circle>
                  )}

                  {/* Semantic target ring */}
                  {isSemanticTarget && !isActive && (
                    <circle
                      cx={node.cx}
                      cy={node.cy}
                      r={radius + 5}
                      fill="none"
                      stroke="#a855f7"
                      strokeWidth={2}
                      opacity={0.6}
                    />
                  )}

                  {/* Glow effect for hovered node */}
                  {isHovered && (
                    <circle
                      cx={node.cx}
                      cy={node.cy}
                      r={radius + 6}
                      fill="none"
                      stroke={color.fill}
                      strokeWidth={2}
                      opacity={0.3}
                    />
                  )}

                  {/* Latest node ring indicator */}
                  {isLast && !selectedAgent && !isActive && !isSemanticTarget && (
                    <circle
                      cx={node.cx}
                      cy={node.cy}
                      r={radius + 4}
                      fill="none"
                      stroke="#f59e0b"
                      strokeWidth={2.5}
                    />
                  )}

                  {/* Node circle */}
                  <circle
                    cx={node.cx}
                    cy={node.cy}
                    r={radius}
                    fill={color.fill}
                    stroke={isActive ? '#1d4ed8' : color.stroke}
                    strokeWidth={isActive ? 2.5 : 1.5}
                    style={{ transition: 'all 0.15s ease' }}
                  />

                  {/* Post number inside circle */}
                  {node.type === 'post' && node.postId && (
                    <text
                      x={node.cx}
                      y={node.cy + 1}
                      textAnchor="middle"
                      dominantBaseline="middle"
                      fill="white"
                      fontSize={20}
                      fontWeight={900}
                      fontFamily="sans-serif"
                      className="pointer-events-none select-none"
                    >
                      {node.postId}
                    </text>
                  )}

                  {/* Post title label */}
                  {node.type === 'post' && node.title && (
                    <text
                      x={node.cx}
                      y={node.cy - radius - 14}
                      textAnchor="middle"
                      fill="#0f172a"
                      fontSize={22}
                      fontWeight={800}
                      fontFamily="system-ui, -apple-system, sans-serif"
                      className="pointer-events-none select-none"
                    >
                      {node.title}
                    </text>
                  )}

                </g>
              );
            })}
          </g>
        </svg>
      </div>

      {/* Hover Tooltip */}
      {hoveredNode && (
        <div
          className="fixed z-50 max-w-lg bg-white rounded-xl shadow-2xl border border-gray-300 pointer-events-none"
          style={{
            left: Math.min(window.innerWidth - 480, Math.max(10, mousePos.x + 16)),
            top: Math.min(window.innerHeight - 200, Math.max(10, mousePos.y - 20)),
            padding: '14px 18px',
          }}
        >
          <div className="flex items-center gap-2 mb-2 pb-2 border-b border-gray-200">
            <span style={{ fontSize: '18px', fontWeight: 700, color: '#111827' }}>{hoveredNode.name}</span>
            <span className="ml-auto" style={{ fontSize: '15px', fontWeight: 600, color: '#4b5563', background: '#f3f4f6', padding: '3px 12px', borderRadius: '5px' }}>{hoveredNode.type}</span>
          </div>
          <p style={{ fontSize: '18px', lineHeight: '1.6', color: '#374151', display: '-webkit-box', WebkitLineClamp: 4, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
            {hoveredNode.summary || hoveredNode.preview || t.noContent}
          </p>
          {hoveredNode.created_at && (
            <p style={{ fontSize: '15px', color: '#4b5563', marginTop: '12px', fontWeight: 500 }}>{formatTime(hoveredNode.created_at)}</p>
          )}
        </div>
      )}
    </div>
  );
});

export default BranchView;
