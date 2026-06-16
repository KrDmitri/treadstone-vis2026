"""
Client Store Management

Manages isolated data stores for each client (browser tab).
Each client has its own:
- posts_db: Feed posts
- post_id_counter: Post ID counter
- reply_id_counter: Reply ID counter
- files: Uploaded files metadata
- sessions: Agent conversation sessions

This ensures complete isolation between different users/tabs.
"""
from typing import Dict, Any, Optional, List
from datetime import datetime
from utils.logger import logger

class ClientStore:
    """Manages data for a single client"""
    
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.created_at = datetime.now()
        self.last_accessed = datetime.now()
        
        # Feed data
        self.posts_db: List[dict] = []
        self.post_id_counter: int = 1
        self.reply_id_counter: int = 1
        
        # File metadata
        self.files: Dict[str, dict] = {}
        
        # Summary trigger state
        self.summary_trigger_state: Dict[str, dict] = {}
        
        #  NEW: Agent registry (client-isolated)
        self.agent_registry: Dict[str, dict] = {}
        self.agent_counter: int = 0
        self._init_default_agents()
        
        #  NEW: Pending messages queue for WebSocket reliability
        # Messages queued when WebSocket is not connected
        self.pending_messages: List[dict] = []
        self.max_pending_messages: int = 100  # Limit to prevent memory issues
        
        #  Language preference - set via Settings page
        # "Auto" = detect from user message, "English"/"Korean" = explicit
        self.language: str = "English"
        
        logger.debug(f"Created new ClientStore for: {client_id}")
    
    def get_next_post_id(self) -> int:
        """Get next post ID and increment counter"""
        post_id = self.post_id_counter
        self.post_id_counter += 1
        return post_id
    
    def get_next_reply_id(self) -> int:
        """Get next reply ID and increment counter"""
        reply_id = self.reply_id_counter
        self.reply_id_counter += 1
        return reply_id
    
    def add_post(self, post: dict) -> dict:
        """Add a post to this client's feed"""
        self.posts_db.append(post)
        self.last_accessed = datetime.now()
        return post
    
    def get_post(self, post_id: int) -> Optional[dict]:
        """Get a post by ID"""
        self.last_accessed = datetime.now()
        return next((p for p in self.posts_db if p.get("id") == post_id), None)
    
    def get_all_posts(self) -> List[dict]:
        """Get all posts sorted by ID"""
        self.last_accessed = datetime.now()
        return sorted(self.posts_db, key=lambda x: x["id"])
    
    def add_file(self, file_id: str, file_metadata: dict):
        """Register a file for this client"""
        self.files[file_id] = file_metadata
        self.last_accessed = datetime.now()
    
    def get_file(self, file_id: str) -> Optional[dict]:
        """Get file metadata by ID"""
        self.last_accessed = datetime.now()
        return self.files.get(file_id)
    
    def get_stats(self) -> dict:
        """Get client store statistics"""
        return {
            "client_id": self.client_id,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "post_count": len(self.posts_db),
            "file_count": len(self.files),
            "total_replies": sum(len(p.get("replies", [])) for p in self.posts_db),
            "pending_messages": len(self.pending_messages)
        }
    
    #  NEW: Pending messages management methods
    def queue_message(self, message: dict):
        """
        Queue a message for later delivery when WebSocket connects.
        
        Args:
            message: WebSocket message to queue
        """
        # Limit queue size to prevent memory issues
        if len(self.pending_messages) >= self.max_pending_messages:
            # Remove oldest messages to make room
            self.pending_messages = self.pending_messages[-(self.max_pending_messages - 1):]
            logger.warning(f"Pending messages queue full for {self.client_id[:20]}..., removed oldest messages")
        
        self.pending_messages.append(message)
        self.last_accessed = datetime.now()
        logger.debug(f"Queued message for {self.client_id[:20]}...: {message.get('type')} (queue size: {len(self.pending_messages)})")
    
    def get_pending_messages(self) -> List[dict]:
        """
        Get and clear all pending messages.
        
        Returns:
            List of pending messages (clears the queue)
        """
        messages = self.pending_messages.copy()
        self.pending_messages = []
        self.last_accessed = datetime.now()
        
        if messages:
            logger.debug(f"Retrieved {len(messages)} pending messages for {self.client_id[:20]}...")
        
        return messages
    
    def has_pending_messages(self) -> bool:
        """Check if there are pending messages."""
        return len(self.pending_messages) > 0
    
    # ==================== Agent Management ====================
    
    # Default agents configuration
    DEFAULT_AGENTS = {
        "statistics": {"name": "Statistical Analyst Agent", "icon": "📊"},
        "visualization": {"name": "Visualization Expert Agent", "icon": "📈"},
        "insight": {"name": "Intelligence Agent", "icon": "💡"},
        "summary": {"name": "Summary Agent", "icon": "📝"},
        "scanner": {"name": "Data Scout Agent", "icon": "🔍"},
    }
    MAX_AGENTS_PER_ROLE = 2
    
    def _init_default_agents(self):
        """Initialize default agents for this client"""
        for role, config in self.DEFAULT_AGENTS.items():
            self.agent_counter += 1
            agent_id = f"agent_{self.agent_counter}"
            self.agent_registry[agent_id] = {
                "id": agent_id,
                "role": role,
                "name": config["name"],
                "icon": config["icon"],
                "is_default": True,
            }
    
    def get_all_agents(self) -> list:
        """Get all agent configurations"""
        return list(self.agent_registry.values())
    
    def get_agents_by_role(self, role: str) -> list:
        """Get all agents for a specific role"""
        return [a for a in self.agent_registry.values() if a["role"] == role]
    
    def can_add_agent(self, role: str) -> bool:
        """Check if we can add another agent to this role"""
        return len(self.get_agents_by_role(role)) < self.MAX_AGENTS_PER_ROLE
    
    def get_available_roles(self) -> list:
        """Get list of roles with add availability"""
        return [
            {
                "role": role,
                "icon": config["icon"],
                "can_add": self.can_add_agent(role),
                "current_count": len(self.get_agents_by_role(role)),
                "max_count": self.MAX_AGENTS_PER_ROLE
            }
            for role, config in self.DEFAULT_AGENTS.items()
        ]
    
    def create_agent(self, role: str, name: str) -> dict:
        """Create a new custom agent"""
        if role not in self.DEFAULT_AGENTS:
            raise ValueError(f"Unknown role: {role}")
        if not self.can_add_agent(role):
            raise ValueError(f"Maximum agents reached for role: {role}")
        
        self.agent_counter += 1
        agent_id = f"agent_{self.agent_counter}"
        agent = {
            "id": agent_id,
            "role": role,
            "name": name,
            "icon": self.DEFAULT_AGENTS[role]["icon"],
            "is_default": False,
        }
        self.agent_registry[agent_id] = agent
        logger.info(f"Agent created for {self.client_id[:20]}...: {name} ({role})")
        return agent
    
    def delete_agent(self, agent_id: str) -> bool:
        """Delete a custom agent (cannot delete defaults)"""
        if agent_id not in self.agent_registry:
            return False
        
        agent = self.agent_registry[agent_id]
        if agent["is_default"]:
            raise ValueError("Cannot delete default agent")
        
        del self.agent_registry[agent_id]
        logger.info(f"Agent deleted for {self.client_id[:20]}...: {agent_id}")
        return True
    
    def update_agent_name(self, agent_id: str, name: str) -> bool:
        """Update agent name by ID"""
        if agent_id in self.agent_registry:
            self.agent_registry[agent_id]["name"] = name
            logger.info(f"Agent updated for {self.client_id[:20]}...: {agent_id} -> {name}")
            return True
        return False
    
    def get_agent_display_name(self, role: str) -> str:
        """Get the display name for an agent role"""
        for agent in self.agent_registry.values():
            if agent["role"] == role:
                return agent["name"]
        return role.title()


class ClientStoreManager:
    """Manages all client stores"""
    
    def __init__(self):
        self.stores: Dict[str, ClientStore] = {}
        self._cleanup_interval_hours = 24  # Auto-cleanup inactive stores
    
    def get_store(self, client_id: str) -> ClientStore:
        """
        Get or create a client store.
        
        Args:
            client_id: Unique client identifier
            
        Returns:
            ClientStore for this client
        """
        if not client_id:
            raise ValueError("client_id is required")
        
        if client_id not in self.stores:
            store = ClientStore(client_id)
            self.stores[client_id] = store
            logger.info(f"Created new store for client: {client_id[:20]}... (Total: {len(self.stores)} stores)")
            
        return self.stores[client_id]
    
    def has_store(self, client_id: str) -> bool:
        """Check if a store exists for this client"""
        return client_id in self.stores
    
    def delete_store(self, client_id: str) -> bool:
        """Delete a client store (for cleanup or reset)"""
        if client_id in self.stores:
            del self.stores[client_id]
            logger.info(f"Deleted store for client: {client_id}")
            return True
        return False
    
    def get_all_stats(self) -> dict:
        """Get statistics for all stores"""
        return {
            "total_clients": len(self.stores),
            "clients": [store.get_stats() for store in self.stores.values()]
        }
    
    def cleanup_inactive(self, hours: int = 24) -> int:
        """
        Remove stores that haven't been accessed in the specified hours.
        Returns number of stores removed.
        """
        from datetime import timedelta
        
        cutoff = datetime.now() - timedelta(hours=hours)
        to_remove = [
            client_id for client_id, store in self.stores.items()
            if store.last_accessed < cutoff
        ]
        
        for client_id in to_remove:
            del self.stores[client_id]
        
        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} inactive client stores")
        
        return len(to_remove)


# Global instance
client_store_manager = ClientStoreManager()


def get_client_store(client_id: str) -> ClientStore:
    """Convenience function to get a client store"""
    return client_store_manager.get_store(client_id)
