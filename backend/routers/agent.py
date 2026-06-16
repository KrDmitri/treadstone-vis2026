"""
AI Agent API endpoints
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models.schemas import AgentAnalyzeRequest, AutoAnalyzeRequest, AgentResponse
from services import agent_service, file_service
from services.websocket_manager import ws_manager
from utils.logger import logger
from config import settings

router = APIRouter(prefix="/api/agent", tags=["agent"])

# Conversation history for debugging
conversation_history = []


@router.post("/analyze", response_model=AgentResponse)
async def analyze_with_agent(request: AgentAnalyzeRequest):
    """
    Interact with AI agents
    
    Args:
        request: Analysis request with message, file_id, context, optional agent_role, and client_id
        
    Returns:
        AgentResponse: Agent's analysis and recommendations
    """
    post_id = request.post_id  # Get post_id for WebSocket typing indicator
    client_id = request.client_id  # Get client_id for isolation
    
    try:
        logger.info(f"Agent analyze request: {request.message[:50]}... (post_id: {post_id}, client: {client_id[:20] if client_id else 'N/A'}...)")
        
        # Get file metadata if file_id provided
        file_metadata = None
        if request.file_id:
            file_metadata = file_service.get_file(request.file_id)
            if not file_metadata:
                raise HTTPException(status_code=404, detail="File not found")
            logger.info(f"Continuing conversation with: {file_metadata.get('original_filename')}")
        
        # Run agent analysis (WebSocket emit is handled inside agents_sdk.py)
        result = await agent_service.analyze(request, file_metadata, post_id=post_id, client_id=client_id)
        
        # Store in conversation history for debugging
        from datetime import datetime
        conversation_history.append({
            "timestamp": datetime.now().isoformat(),
            "type": "user_analyze",
            "file": file_metadata.get('original_filename') if file_metadata else None,
            "user_input": request.message,
            "agent": result.agent,
            "agent_role": result.agent_role,
            "agent_response": result.content
        })
        
        logger.info(f"Analysis complete: {result.agent}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in analyze_with_agent: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent analysis failed: {str(e)}")


@router.post("/auto-analyze-csv", response_model=AgentResponse)
async def auto_analyze_file(request: AutoAnalyzeRequest):
    """
    Automatically analyze uploaded file
    
    Args:
        request: Request with file_id
        
    Returns:
        AgentResponse: Automatic analysis results
    """
    try:
        logger.info(f"Received auto-analyze request for file_id: {request.file_id}")
        
        # Get file metadata
        file_metadata = file_service.get_file(request.file_id)
        if not file_metadata:
            logger.error(f"File not found: {request.file_id}")
            raise HTTPException(status_code=404, detail="File not found")
        
        logger.info(f"File found: {file_metadata.get('original_filename')}")
        
        # Run automatic analysis
        result = await agent_service.auto_analyze_file(file_metadata)
        
        # Store in conversation history for debugging
        from datetime import datetime
        conversation_history.append({
            "timestamp": datetime.now().isoformat(),
            "type": "auto_analyze",
            "file": file_metadata.get('original_filename'),
            "agent": result.agent,
            "agent_role": result.agent_role,
            "user_input": f"Auto-analyze: {file_metadata.get('original_filename')}",
            "agent_response": result.content
        })
        
        logger.info(f"Auto-analysis complete")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in auto_analyze_file: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Auto analysis failed: {str(e)}")


@router.post("/proactive-scan")
async def trigger_proactive_scan(request: AutoAnalyzeRequest):
    """
    Trigger initial proactive scan when data is uploaded
    
    Args:
        request: Request with file_id and client_id
        
    Returns:
        Post data for the proactive scan
    """
    client_id = request.client_id  # Get client_id for isolation
    
    try:
        logger.info(f"Triggering initial proactive scan for file_id: {request.file_id} (client: {client_id[:20] if client_id else 'N/A'}...)")
        
        # Get file metadata
        file_metadata = file_service.get_file(request.file_id)
        if not file_metadata:
            raise HTTPException(status_code=404, detail="File not found")
        
        # Get file path from metadata
        file_path = file_metadata.get('file_path')
        if not file_path:
            raise HTTPException(status_code=500, detail="File path not found in metadata")
        
        # ============ FIND USER'S ORIGINAL GOAL ============
        from services.client_store import get_client_store
        store = get_client_store(client_id) if client_id else None
        
        user_goal = None
        if store:
            for p in store.posts_db:
                if (p.get("author_type") == "user" and 
                    p.get("file_metadata", {}).get("file_id") == request.file_id):
                    user_goal = p.get("content", "")
                    logger.info(f"Found user's analysis goal: {user_goal[:50]}...")
                    break
        
        # Run proactive analysis (WebSocket emit is handled inside agents_sdk.py)
        from agents_sdk import run_proactive_analysis
        
        result = await run_proactive_analysis(
            previous_discussion=None,
            file_id=request.file_id,
            file_path=file_path,
            client_id=client_id,
            user_goal=user_goal
        )
        
        logger.info(f"Initial proactive scan complete: {len(result.get('content', ''))} chars")
        
        # Return post data (frontend will create the actual post)
        return {
            "author": "Data Scout Agent",
            "author_type": "agent",
            "author_role": "scanner",
            "content": result.get('content', ''),
            "visualization": result.get('visualization'),
            "file_metadata": file_metadata
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in proactive scan: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Proactive scan failed: {str(e)}")


@router.post("/check-proactive-viz")
async def check_and_maybe_add_visualization(request: dict):
    """
    Check if an agent reply would benefit from visualization and add if needed
    
    This is called after a reactive agent responds to see if visualization would help
    
    Args:
        request: {
            "agent_reply_content": str,
            "agent_name": str,
            "post_id": int,
            "file_id": str
        }
    
    Returns:
        Visualization reply if needed, or None
    """
    try:
        from services.orchestration_service import should_add_proactive_visualization
        from agents_sdk import run_agent_analysis, visualizer_agent
        
        agent_reply_content = request.get("agent_reply_content")
        agent_name = request.get("agent_name")
        file_id = request.get("file_id")
        
        if not all([agent_reply_content, agent_name, file_id]):
            raise HTTPException(status_code=422, detail="Missing required fields")
        
        # Check if visualization would help
        viz_decision = await should_add_proactive_visualization(
            agent_reply_content=agent_reply_content,
            agent_name=agent_name
        )
        
        if not viz_decision.get('should_visualize'):
            logger.info(f"No proactive visualization needed for {agent_name}")
            return {"should_add": False, "reason": viz_decision.get('reason')}
        
        logger.info(f"Adding proactive visualization for {agent_name}")
        logger.info(f"Chart type: {viz_decision.get('chart_description')}")
        
        # Get file metadata
        file_metadata = file_service.get_file(file_id)
        if not file_metadata:
            raise HTTPException(status_code=404, detail="File not found")
        
        file_path = file_metadata.get('file_path')
        
        # Create visualization
        viz_request = f"Create a visualization to enhance this analysis: {agent_reply_content[:200]}... "
        viz_request += f"Specifically: {viz_decision.get('chart_description', 'appropriate chart')}"
        
        viz_result = await run_agent_analysis(
            message=viz_request,
            file_path=file_path,
            file_id=file_id,
            agent=visualizer_agent
        )
        
        logger.info(f"Proactive visualization created")
        
        return {
            "should_add": True,
            "agent": viz_result.get('agent', 'Visualization Expert Agent'),
            "agent_role": "visualization",
            "content": viz_result.get('content', ''),
            "visualization": viz_result.get('visualization')
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Check proactive viz failed: {str(e)}", exc_info=True)
        return {"should_add": False, "error": str(e)}


@router.get("/conversation-history")
async def get_conversation_history():
    """
    Get conversation history for debugging
    
    Returns:
        List of conversation exchanges between user and agents
    """
    return {
        "total": len(conversation_history),
        "conversations": conversation_history[-20:]  # Last 20 conversations
    }


# ============ AGENT CONFIGURATION ENDPOINTS ============

class AgentConfigUpdateRequest(BaseModel):
    agent_key: str
    model: str


@router.get("/config")
async def get_agent_configs():
    """
    Get all agent configurations
    
    Returns:
        dict: Agent configurations with model settings
    """
    try:
        logger.info("Fetching agent configurations")
        return settings.AGENT_CONFIGS
    except Exception as e:
        logger.error(f"Failed to fetch agent configs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def update_agent_config(request: AgentConfigUpdateRequest):
    """
    Update agent configuration
    
    Args:
        request: Agent key and new model setting
        
    Returns:
        Updated configuration
    """
    try:
        agent_key = request.agent_key
        model = request.model
        
        # Validate agent key
        valid_agents = ['scanner', 'statistics', 'visualization', 'insight', 'summary']
        if agent_key not in valid_agents:
            raise HTTPException(status_code=400, detail=f"Invalid agent key: {agent_key}")
        
        # Validate model (allow GPT-5.x, GPT-4.x, and GPT-3.5)
        valid_models = ['gpt-5.2', 'gpt-5.1', 'gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo']
        if model not in valid_models:
            raise HTTPException(status_code=400, detail=f"Invalid model: {model}. Valid: {valid_models}")
        
        # Update configuration
        settings.set_agent_model(agent_key, model)
        logger.info(f"Updated {agent_key} agent model to {model}")
        
        # TODO: Reload agents with new models (requires agent recreation)
        # For now, changes will apply on next agent initialization
        
        return {
            "success": True,
            "agent_key": agent_key,
            "model": model,
            "message": "Agent configuration updated. Changes will apply to new conversations."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update agent config: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
