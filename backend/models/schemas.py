"""
Pydantic models for request/response validation
"""
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

# ==================== Post Models ====================

class Post(BaseModel):
    """Post model"""
    id: int
    author: str
    author_type: str  # "user" or "agent"
    author_role: Optional[str] = None  # For agents: "statistics", "visualization", "insight", "scanner"
    content: str
    created_at: str
    likes: int = 0
    replies: List["Reply"] = []
    visualization: Optional[dict] = None
    hitl_options: Optional[List[str]] = None
    file_metadata: Optional[dict] = None
    references_post_id: Optional[int] = None  # For proactive posts: references the previous discussion

class Reply(BaseModel):
    """Reply model"""
    id: int
    post_id: int
    author: str
    author_type: str
    author_role: Optional[str] = None  # For agents: "statistics", "visualization", "insight", "scanner"
    content: str
    created_at: str
    likes: int = 0
    visualization: Optional[dict] = None  # For agent replies with charts

# ==================== File Models ====================

class FileMetadata(BaseModel):
    """File metadata model"""
    file_id: str
    original_filename: str
    file_type: str  # "csv", "txt", "jpg", "png", etc.
    size: int
    rows: Optional[int] = None  # For CSV files
    columns: Optional[int] = None  # For CSV files
    column_names: Optional[List[str]] = None  # For CSV files
    line_count: Optional[int] = None  # For TXT files
    uploaded_at: Optional[str] = None
    file_path: Optional[str] = None
    openai_file_id: Optional[str] = None
    client_id: Optional[str] = None  # For client isolation
    # Image-related fields
    is_image: Optional[bool] = None
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None
    image_url: Optional[str] = None
    image_base64_url: Optional[str] = None

# ==================== Agent Models ====================

class AgentAnalyzeRequest(BaseModel):
    """Request for agent analysis"""
    message: str
    file_id: Optional[str] = None
    context: Optional[dict] = None
    agent_role: Optional[str] = None  # Optional core agent role
    post_id: Optional[int] = None  # For WebSocket typing indicator - which post is being replied to
    client_id: Optional[str] = None  # For client isolation

class AutoAnalyzeRequest(BaseModel):
    """Request for automatic analysis when file is uploaded"""
    file_id: str
    client_id: Optional[str] = None  # For client isolation

class AgentResponse(BaseModel):
    """Response from agent analysis"""
    agent: str
    agent_role: str
    content: str
    visualization: Optional[dict] = None
    hitl_options: List[str] = []

# ==================== Summary Models ====================

class Summary(BaseModel):
    """Summary model for conversation summary"""
    id: int
    file_id: str
    content: str  # Markdown formatted summary
    created_at: str
    updated_at: str
    post_count: int  # Number of posts at summary generation
    reply_count: int  # Number of replies at summary generation

class GenerateSummaryRequest(BaseModel):
    """Request to generate summary"""
    file_id: str

class NextStepCard(BaseModel):
    """Individual next step card"""
    id: int
    icon: str
    title: str
    question: str
    description: str
    target_agent_role: Optional[str] = None


class SummaryResponse(BaseModel):
    """Response for summary generation"""
    success: bool
    file_id: str
    content: Optional[str] = None
    next_steps: Optional[List[NextStepCard]] = None  # List of next step cards
    error: Optional[str] = None
    created_at: Optional[str] = None
    post_count: Optional[int] = None
    reply_count: Optional[int] = None
