import { useEffect, useState, useCallback } from 'react';
import { getClientId } from '../utils/clientId';
import axios from 'axios';

/**
 * ==================== SINGLETON WEBSOCKET MANAGER ====================
 * Single WebSocket connection shared across all components
 * 
 *  SINGLETON PATTERN: Ensures only ONE WebSocket connection per tab
 * All components that call useWebSocket() share the same connection and state
 */

// Module-level singleton state
let globalWs = null;
let globalIsConnected = false;
let globalLastMessage = null;
let globalTypingAgents = {};
let globalTypingNewPost = null;
let globalClientId = null;
let globalReconnectTimeout = null;
let globalPingInterval = null;

// Subscribers for state changes
const subscribers = new Set();

function notifySubscribers() {
    subscribers.forEach(callback => callback());
}

function getDefaultWsUrl() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    return `${protocol}//${host}/ws`;
}

function initWebSocket() {
    if (globalWs && (globalWs.readyState === WebSocket.OPEN || globalWs.readyState === WebSocket.CONNECTING)) {
        return; // Already connected or connecting
    }

    if (!globalClientId) {
        globalClientId = getClientId();
    }

    const wsUrl = `${getDefaultWsUrl()}?client_id=${encodeURIComponent(globalClientId)}`;


    try {
        globalWs = new WebSocket(wsUrl);

        globalWs.onopen = () => {
            globalIsConnected = true;
            notifySubscribers();

            // Ping interval to keep connection alive
            if (globalPingInterval) {
                clearInterval(globalPingInterval);
            }
            globalPingInterval = setInterval(() => {
                if (globalWs?.readyState === WebSocket.OPEN) {
                    globalWs.send('ping');
                }
            }, 30000);
        };

        globalWs.onclose = () => {
            globalIsConnected = false;
            notifySubscribers();

            if (globalPingInterval) {
                clearInterval(globalPingInterval);
                globalPingInterval = null;
            }

            // Auto-reconnect after 3 seconds
            if (globalReconnectTimeout) {
                clearTimeout(globalReconnectTimeout);
            }
            globalReconnectTimeout = setTimeout(() => {
                initWebSocket();
            }, 3000);
        };

        globalWs.onerror = (error) => {
            console.error('[SINGLETON] WebSocket error:', error);
        };

        globalWs.onmessage = (event) => {
            if (event.data === 'pong') return;

            try {
                const message = JSON.parse(event.data);

                globalLastMessage = message;

                // Handle agent_typing
                if (message.type === 'agent_typing') {
                    const { agent, post_id, status, context, role } = message;

                    if (context === 'post') {
                        if (status === 'start') {
                            globalTypingNewPost = { agent, role, startTime: Date.now() };
                        } else if (status === 'end') {
                            globalTypingNewPost = null;
                        }
                    } else {
                        // reply context
                        if (status === 'start') {
                            globalTypingAgents = {
                                ...globalTypingAgents,
                                [post_id || 'global']: { agent, role, startTime: Date.now() }
                            };
                        } else if (status === 'end') {
                            const updated = { ...globalTypingAgents };
                            delete updated[post_id || 'global'];
                            globalTypingAgents = updated;
                        }
                    }
                }

                notifySubscribers();
            } catch (e) {
                console.error('Failed to parse WebSocket message:', e);
            }
        };
    } catch (error) {
        console.error('Failed to create WebSocket:', error);
    }
}

// Auto-clear stale typing indicators (safety net)
setInterval(() => {
    const now = Date.now();
    let hasChanges = false;

    // Clear stale typing agents (older than 60 seconds)
    const updatedAgents = { ...globalTypingAgents };
    Object.entries(updatedAgents).forEach(([postId, data]) => {
        if (now - data.startTime > 60000) {
            delete updatedAgents[postId];
            hasChanges = true;
        }
    });
    if (hasChanges) {
        globalTypingAgents = updatedAgents;
    }

    // Clear stale new post typing
    if (globalTypingNewPost && now - globalTypingNewPost.startTime > 60000) {
        globalTypingNewPost = null;
        hasChanges = true;
    }

    if (hasChanges) {
        notifySubscribers();
    }
}, 10000);

/**
 * WebSocket hook for real-time updates with client isolation
 * 
 *  SINGLETON PATTERN: All components share the same WebSocket connection
 * 
 * @returns {object} - { isConnected, lastMessage, typingAgents, typingNewPost, sendMessage, clientId, fetchPendingMessages }
 */
export function useWebSocket() {
    // Force re-render when global state changes
    const [, forceUpdate] = useState(0);

    useEffect(() => {
        const callback = () => forceUpdate(n => n + 1);
        subscribers.add(callback);

        // Initialize WebSocket on first hook usage
        if (!globalWs || globalWs.readyState === WebSocket.CLOSED) {
            initWebSocket();
        }

        return () => {
            subscribers.delete(callback);
        };
    }, []);

    const clientId = globalClientId || getClientId();

    const sendMessage = useCallback((message) => {
        if (globalWs?.readyState === WebSocket.OPEN) {
            globalWs.send(typeof message === 'string' ? message : JSON.stringify(message));
        }
    }, []);

    const disconnect = useCallback(() => {
        if (globalReconnectTimeout) {
            clearTimeout(globalReconnectTimeout);
            globalReconnectTimeout = null;
        }
        if (globalPingInterval) {
            clearInterval(globalPingInterval);
            globalPingInterval = null;
        }
        if (globalWs) {
            globalWs.close();
            globalWs = null;
        }
    }, []);

    const reconnect = useCallback(() => {
        disconnect();
        initWebSocket();
    }, [disconnect]);

    // Fetch pending messages via API (backup method)
    const fetchPendingMessages = useCallback(async () => {
        try {
            const response = await axios.get('/api/pending-messages', {
                params: { client_id: clientId }
            });

            const messages = response.data.messages || [];

            if (messages.length > 0) {

                // Process each pending message
                for (const message of messages) {
                    globalLastMessage = message;

                    // Handle agent_typing messages
                    if (message.type === 'agent_typing') {
                        const { agent, post_id, status, context } = message;

                        if (context === 'post') {
                            if (status === 'start') {
                                globalTypingNewPost = { agent, startTime: Date.now() };
                            } else if (status === 'end') {
                                globalTypingNewPost = null;
                            }
                        } else {
                            if (status === 'start') {
                                globalTypingAgents = {
                                    ...globalTypingAgents,
                                    [post_id || 'global']: { agent, startTime: Date.now() }
                                };
                            } else if (status === 'end') {
                                const updated = { ...globalTypingAgents };
                                delete updated[post_id || 'global'];
                                globalTypingAgents = updated;
                            }
                        }
                    }
                }
                notifySubscribers();
            }

            return messages;
        } catch (error) {
            console.error('Failed to fetch pending messages:', error);
            return [];
        }
    }, [clientId]);

    return {
        isConnected: globalIsConnected,
        lastMessage: globalLastMessage,
        typingAgents: globalTypingAgents,
        typingNewPost: globalTypingNewPost,
        sendMessage,
        disconnect,
        reconnect,
        clientId,
        fetchPendingMessages
    };
}

export default useWebSocket;
