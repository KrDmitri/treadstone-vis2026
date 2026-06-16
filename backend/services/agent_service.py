"""
Agent management service
Handles AI agent interactions using OpenAI Agents SDK
"""
from typing import Optional, Dict, Any
from pathlib import Path

from agents_sdk import (
    get_agent_display_name,
    run_agent_analysis,
    run_agent_analysis_sync,
    analyst_agent,
    visualizer_agent,
    insight_agent,
    get_agent_by_role,
    generate_hitl_options,
    parse_mentions,
)
from models.schemas import AgentAnalyzeRequest, AgentResponse
from utils.logger import logger


class AgentService:
    """Service for managing AI agent interactions"""
    
    async def analyze(
        self,
        request: AgentAnalyzeRequest,
        file_metadata: Optional[dict] = None,
        post_id: Optional[int] = None,
        data_scope: Optional[Dict[str, Any]] = None,
        client_id: Optional[str] = None,
    ) -> AgentResponse:
        """
        Analyze user request with appropriate AI agent
        
        Args:
            request: Analysis request
            file_metadata: Optional file metadata if file is attached
            post_id: Optional post ID for session management
            data_scope: Optional data scope for filtering queries
            client_id: Optional client ID for session isolation
            
        Returns:
            AgentResponse: Agent response
        """
        logger.info(f"Starting agent analysis (client: {client_id[:20] if client_id else 'N/A'}...)")
        logger.debug(f"Message: {request.message[:100]}...")
        
        if data_scope:
            logger.info(f"Data scope active: {data_scope}")
        
        # Prepare file information
        file_path = None
        openai_file_id = None
        image_file_path = None  # NEW: for attached images
        
        if file_metadata:
            file_path = file_metadata.get("file_path")
            openai_file_id = file_metadata.get("openai_file_id")
            image_file_path = file_metadata.get("image_file_path")  # NEW: get attached image path
            logger.info(f"File attached: {file_metadata.get('original_filename')} (OpenAI ID: {openai_file_id})")
        
        message = request.message
        display_name = None
        agent_role = request.agent_role

        if not agent_role:
            mentioned_role, mentioned_agent_id, cleaned_message = parse_mentions(message, client_id)
            if mentioned_role:
                agent_role = mentioned_role
                message = cleaned_message
                if mentioned_agent_id and client_id:
                    try:
                        from services.client_store import get_client_store
                        store = get_client_store(client_id)
                        specific_agent = store.agent_registry.get(mentioned_agent_id)
                        display_name = specific_agent["name"] if specific_agent else None
                    except Exception:
                        display_name = None
                display_name = display_name or get_agent_display_name(agent_role, client_id)
                logger.info(f"@Mention detected in agent API: role {agent_role} -> '{display_name}'")

        # Get specific agent if requested
        agent = None
        if agent_role:
            agent = get_agent_by_role(agent_role)
            logger.info(f"Using specific agent: {agent.name}")
        else:
            logger.info("Using internal request router")
        
        # Run agent analysis with file path
        logger.info(f"Running agent analysis...")
        logger.debug(f"Passing file_path: {file_path}, file_id: {request.file_id}, openai_file_id: {openai_file_id}, post_id: {post_id}, client_id: {client_id[:20] if client_id else 'N/A'}...")
        result = await run_agent_analysis(
            message=message,
            file_path=file_path,
            file_id=request.file_id,  # Use local file_id, not openai_file_id
            agent=agent,
            post_id=post_id,
            data_scope=data_scope,
            image_file_path=image_file_path,
            client_id=client_id,
            display_name=display_name,
            agent_role=agent_role,
        )
        
        logger.info(f"Agent response from: {result['agent']}")
        logger.debug(f"Response content: {result['content'][:200]}...")
        
        return AgentResponse(**result)
    
    async def auto_analyze_file(
        self,
        file_metadata: dict
    ) -> AgentResponse:
        """
        Automatically analyze uploaded file
        
        Args:
            file_metadata: File metadata
            
        Returns:
            AgentResponse: Agent analysis
        """
        file_type = file_metadata.get("file_type", "unknown")
        filename = file_metadata.get("original_filename", "unknown")
        
        logger.info(f"Auto-analyzing file: {filename} (type: {file_type})")
        
        # Create analysis message based on file type
        if file_type == "csv":
            rows = file_metadata.get("rows", 0)
            columns = file_metadata.get("columns", 0)
            message = (
                f"A new CSV dataset has been uploaded: '{filename}'. "
                f"It contains {rows} rows and {columns} columns. "
                f"Please analyze this data and provide key statistical insights."
            )
            logger.info(f"CSV file: {rows} rows × {columns} columns")
        elif file_type == "txt":
            line_count = file_metadata.get("line_count", 0)
            message = (
                f"A new text document has been uploaded: '{filename}'. "
                f"It contains {line_count} lines. "
                f"Please analyze this document and extract key insights, patterns, and important entities."
            )
            logger.info(f"TXT file: {line_count} lines")
        else:
            message = f"A new file has been uploaded: '{filename}'. Please analyze it."
            logger.warning(f"Unknown file type: {file_type}")
        
        # Create request for Agent A1 (Statistical Analyst for data, general for text)
        agent_role = "statistics" if file_type == "csv" else None
        
        logger.debug(f"Auto-analysis message: {message}")
        
        request = AgentAnalyzeRequest(
            message=message,
            file_id=file_metadata.get("file_id"),
            agent_role=agent_role
        )
        
        return await self.analyze(request, file_metadata)
