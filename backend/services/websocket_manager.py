"""
WebSocket Connection Manager
Handles WebSocket connections and broadcasts events to specific clients.

Now supports client-isolated messaging:
- Each client (browser tab) has its own WebSocket connection
- Messages are sent only to the intended client

Message queuing for reliability:
- When WebSocket is not connected, messages are queued in ClientStore
- When client reconnects, pending messages are delivered
"""
from fastapi import WebSocket
from typing import List, Dict, Any, Optional
import json
from utils.logger import logger


class ConnectionManager:
    """Manages WebSocket connections with client isolation."""
    
    def __init__(self):
        # Map of client_id → WebSocket connection
        self.client_connections: Dict[str, WebSocket] = {}
        # Fallback: list of connections without client_id (for backwards compatibility)
        self.anonymous_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket, client_id: Optional[str] = None):
        """
        Accept a new WebSocket connection.
        Delivers any pending messages after connection.
        
        Args:
            websocket: WebSocket connection
            client_id: Optional client ID for isolation
        """
        await websocket.accept()
        
        if client_id:
            # Store with client_id for targeted messaging
            self.client_connections[client_id] = websocket
            logger.debug(f"WebSocket connected: {client_id[:20]}... (Total: {len(self.client_connections)} clients)")
            
            await self._deliver_pending_messages(client_id)
        else:
            # Fallback for connections without client_id
            self.anonymous_connections.append(websocket)
            logger.debug(f"Anonymous WebSocket connected. Total anonymous: {len(self.anonymous_connections)}")
    
    async def _deliver_pending_messages(self, client_id: str):
        """
        Deliver any pending messages to a newly connected client.
        
        Args:
            client_id: Client ID
        """
        try:
            from services.client_store import client_store_manager
            
            if not client_store_manager.has_store(client_id):
                return
            
            store = client_store_manager.get_store(client_id)
            pending = store.get_pending_messages()
            
            if not pending:
                return
            
            logger.debug(f"Delivering {len(pending)} pending messages to {client_id[:20]}...")
            
            websocket = self.client_connections.get(client_id)
            if not websocket:
                # Re-queue if disconnected
                for msg in pending:
                    store.queue_message(msg)
                return
            
            for message in pending:
                # Skip stale typing events — they're only meaningful in real-time
                if message.get("type") == "agent_typing":
                    logger.debug(f"Skipping stale agent_typing on reconnect")
                    continue
                try:
                    await websocket.send_text(json.dumps(message))
                    logger.debug(f"Delivered pending: {message.get('type')}")
                except Exception as e:
                    logger.warning(f"Failed to deliver pending message: {e}")
                    store.queue_message(message)
            
            logger.debug(f"Delivered all pending messages to {client_id[:20]}...")
            
        except Exception as e:
            logger.error(f"Error delivering pending messages: {e}")
    
    def disconnect(self, websocket: WebSocket, client_id: Optional[str] = None):
        """Remove a WebSocket connection."""
        if client_id and client_id in self.client_connections:
            del self.client_connections[client_id]
            logger.debug(f"WebSocket disconnected: {client_id[:20]}... (Remaining: {len(self.client_connections)} clients)")
        elif websocket in self.anonymous_connections:
            self.anonymous_connections.remove(websocket)
            logger.debug(f"Anonymous WebSocket disconnected. Remaining: {len(self.anonymous_connections)}")
        else:
            # Try to find by websocket object
            for cid, ws in list(self.client_connections.items()):
                if ws == websocket:
                    del self.client_connections[cid]
                    logger.debug(f"WebSocket disconnected by object: {cid[:20]}...")
                    return
    
    def get_client_id_by_websocket(self, websocket: WebSocket) -> Optional[str]:
        """Find client_id by websocket object."""
        for client_id, ws in self.client_connections.items():
            if ws == websocket:
                return client_id
        return None
    
    async def send_to_client(self, client_id: str, message: Dict[str, Any]):
        """
        Send a message to a specific client.
        If client is not connected, queue the message for later delivery.
        
        Args:
            client_id: Target client ID
            message: Message to send
        """
        if client_id not in self.client_connections:
            #  NEW: Queue message for later delivery
            self._queue_message_for_client(client_id, message)
            return
        
        try:
            await self.client_connections[client_id].send_text(json.dumps(message))
        except Exception as e:
            logger.warning(f"Failed to send to client {client_id[:20]}...: {e}")
            #  Queue the message before disconnecting
            self._queue_message_for_client(client_id, message)
            self.disconnect(None, client_id)
    
    def _queue_message_for_client(self, client_id: str, message: Dict[str, Any]):
        """
        Queue a message for a client that is not currently connected.
        
        Args:
            client_id: Target client ID
            message: Message to queue
        """
        try:
            from services.client_store import client_store_manager
            
            # Only queue if client store exists (client has been active before)
            if client_store_manager.has_store(client_id):
                store = client_store_manager.get_store(client_id)
                store.queue_message(message)
                logger.debug(f"Queued {message.get('type')} for disconnected client {client_id[:20]}...")
            else:
                logger.warning(f"Client {client_id[:20]}... has no store, message dropped: {message.get('type')}")
        except Exception as e:
            logger.error(f"Failed to queue message: {e}")
    
    async def broadcast(self, message: Dict[str, Any], client_id: Optional[str] = None):
        """
        Broadcast a message.
        
        If client_id is provided, sends only to that client.
        Otherwise, broadcasts to all connected clients (for backwards compatibility).
        
        Args:
            message: Message to send
            client_id: Optional target client ID
        """
        if client_id:
            # Send to specific client
            await self.send_to_client(client_id, message)
            return
        
        # Broadcast to all (fallback for backwards compatibility)
        message_str = json.dumps(message)
        disconnected = []
        
        # Send to all client connections
        for cid, connection in list(self.client_connections.items()):
            try:
                await connection.send_text(message_str)
            except Exception as e:
                logger.warning(f"Failed to send to {cid[:20]}...: {e}")
                disconnected.append(cid)
        
        # Send to anonymous connections
        for connection in self.anonymous_connections:
            try:
                await connection.send_text(message_str)
            except Exception as e:
                logger.warning(f"Failed to send to anonymous WebSocket: {e}")
                if connection not in disconnected:
                    self.anonymous_connections.remove(connection)
        
        # Clean up disconnected clients
        for cid in disconnected:
            if cid in self.client_connections:
                del self.client_connections[cid]
    
    async def emit_agent_typing(
        self, 
        agent: str, 
        post_id: Optional[int], 
        status: str = "start",
        context: str = "reply",
        client_id: Optional[str] = None,
        role: Optional[str] = None
    ):
        """
        Emit agent typing event.
        
        Args:
            agent: Agent display name
            post_id: Post ID (None if creating a new post)
            status: "start" or "end"
            context: "post" for new post creation, "reply" for replying to a post
            client_id: Target client ID (for isolation)
            role: Agent role for status row display
        """
        message = {
            "type": "agent_typing",
            "agent": agent,
            "post_id": post_id,
            "status": status,
            "context": context,
            "role": role
        }
        
        if client_id:
            await self.send_to_client(client_id, message)
            logger.debug(f"Emitted agent_typing to {client_id[:20]}...: {agent} ({status}) - {context}")
        else:
            await self.broadcast(message)
            logger.debug(f"Broadcast agent_typing: {agent} ({status}) - {context} for post {post_id}")
    
    async def emit_new_post(self, post: Dict[str, Any], client_id: Optional[str] = None):
        """Emit new post created event."""
        message = {
            "type": "new_post",
            "post": post
        }
        
        if client_id:
            await self.send_to_client(client_id, message)
            logger.debug(f"Emitted new_post to {client_id[:20]}...: {post.get('id')}")
        else:
            await self.broadcast(message)
            logger.debug(f"Broadcast new_post: {post.get('id')}")
    
    async def emit_new_reply(self, post_id: int, reply: Dict[str, Any], client_id: Optional[str] = None):
        """Emit new reply created event."""
        message = {
            "type": "new_reply",
            "post_id": post_id,
            "reply": reply
        }
        
        if client_id:
            await self.send_to_client(client_id, message)
            logger.debug(f"Emitted new_reply to {client_id[:20]}...: {reply.get('id')} for post {post_id}")
        else:
            await self.broadcast(message)
            logger.debug(f"Broadcast new_reply: {reply.get('id')} for post {post_id}")
    
    async def emit_feed_updated(self, client_id: Optional[str] = None):
        """Emit generic feed update event (triggers refetch)."""
        message = {"type": "feed_updated"}
        
        if client_id:
            await self.send_to_client(client_id, message)
            logger.debug(f"Emitted feed_updated to {client_id[:20]}...")
        else:
            await self.broadcast(message)
            logger.debug("Broadcast feed_updated")
    
    async def emit_reply_tags_updated(self, post_id: int, reply_id: int, tags: List[str], client_id: Optional[str] = None):
        """Emit reply tags updated event."""
        message = {
            "type": "reply_tags_updated",
            "post_id": post_id,
            "reply_id": reply_id,
            "tags": tags
        }
        
        if client_id:
            await self.send_to_client(client_id, message)
            logger.debug(f"Emitted reply_tags_updated to {client_id[:20]}...: reply {reply_id}")
        else:
            await self.broadcast(message)
            logger.debug(f"Broadcast reply_tags_updated: reply {reply_id} in post {post_id} -> {tags}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get connection statistics."""
        return {
            "client_connections": len(self.client_connections),
            "anonymous_connections": len(self.anonymous_connections),
            "clients": list(self.client_connections.keys())
        }


# Global instance
ws_manager = ConnectionManager()
