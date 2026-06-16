"""
Agent Management API endpoints
Create, update, delete agents (client-isolated)
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from services.client_store import get_client_store
from utils.logger import logger

router = APIRouter(prefix="/api/agents", tags=["agents"])


class CreateAgentRequest(BaseModel):
    role: str
    name: str


class UpdateAgentRequest(BaseModel):
    name: str


@router.get("")
async def get_all_agents(client_id: str = Query(..., description="Client ID for session isolation")):
    """
    Get all agent configurations with available roles (client-isolated)
    """
    try:
        store = get_client_store(client_id)
        
        agents = store.get_all_agents()
        roles = store.get_available_roles()
        
        return {
            "agents": agents,
            "available_roles": roles,
            "max_per_role": store.MAX_AGENTS_PER_ROLE,
            "success": True
        }
    except Exception as e:
        logger.error(f"Failed to get agents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def create_new_agent(
    request: CreateAgentRequest,
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """
    Create a new custom agent for a role (client-isolated)
    """
    try:
        store = get_client_store(client_id)
        
        if not request.name.strip():
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        
        agent = store.create_agent(request.role, request.name.strip())
        
        return {
            "success": True,
            "agent": agent,
            "message": f"Agent '{agent['name']}' created"
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create agent: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{agent_id}")
async def delete_existing_agent(
    agent_id: str,
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """
    Delete a custom agent (default agents cannot be deleted, client-isolated)
    """
    try:
        store = get_client_store(client_id)
        
        success = store.delete_agent(agent_id)
        
        if success:
            return {
                "success": True,
                "message": f"Agent {agent_id} deleted"
            }
        else:
            raise HTTPException(status_code=404, detail="Agent not found")
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete agent: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{agent_id}")
async def update_agent(
    agent_id: str,
    request: UpdateAgentRequest,
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """
    Update an agent's display name by ID (client-isolated)
    """
    try:
        store = get_client_store(client_id)
        
        if agent_id not in store.agent_registry:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        
        if not request.name.strip():
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        
        success = store.update_agent_name(agent_id, request.name.strip())
        
        if success:
            return {
                "success": True,
                "agent_id": agent_id,
                "name": request.name.strip(),
                "message": "Agent updated successfully"
            }
        else:
            raise HTTPException(status_code=400, detail="Failed to update")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update agent: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

