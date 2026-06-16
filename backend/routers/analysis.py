"""
Analysis API routes for summary generation and insights
"""
from fastapi import APIRouter, HTTPException, Query
from datetime import datetime

from models.schemas import GenerateSummaryRequest, SummaryResponse
from agents_sdk import generate_summary
from services.client_store import client_store_manager
from utils.logger import logger

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

# In-memory storage for summaries (will be replaced with DB)
summaries_db: dict[str, dict] = {}  # file_id -> summary data
summary_id_counter = 0


def get_next_summary_id() -> int:
    """Get the next summary ID and increment counter"""
    global summary_id_counter
    summary_id_counter += 1
    return summary_id_counter


def update_summary_id(new_id: int):
    """Update the summary ID counter (if needed for consistency)"""
    global summary_id_counter
    if new_id > summary_id_counter:
        summary_id_counter = new_id


@router.get("/summary/{file_id}")
async def get_summary(file_id: str, client_id: str = Query(...)):
    """
    Get the latest summary for a file
    
    Args:
        file_id: File ID to get summary for
        client_id: Client ID for isolation
        
    Returns:
        Summary data or 404 if not found
    """
    # Reduced logging for polling endpoint
    logger.debug(f"GET summary for file: {file_id}, client: {client_id[:20]}...")
    
    # Use client-specific summaries_db key
    summary_key = f"{client_id}_{file_id}"
    
    if summary_key not in summaries_db:
        raise HTTPException(status_code=404, detail="No summary found for this file")
    
    summary_data = summaries_db[summary_key]
    
    return {
        "success": True,
        **summary_data
    }


@router.post("/summary")
async def create_summary(request: GenerateSummaryRequest, client_id: str = Query(...)):
    """
    Generate a new summary for a file
    
    Args:
        request: GenerateSummaryRequest with file_id
        client_id: Client ID for isolation
        
    Returns:
        SummaryResponse with generated summary
    """
    global summary_id_counter
    
    logger.info(f"POST summary generation for file: {request.file_id}, client: {client_id[:20]}...")
    
    try:
        # Generate summary for the Analysis Hub.
        result = await generate_summary(request.file_id, client_id)
        
        if not result.get('success'):
            raise HTTPException(
                status_code=500, 
                detail=result.get('error', 'Failed to generate summary')
            )
        
        # Get post/reply counts from ClientStore
        store = client_store_manager.get_store(client_id)
        
        related_posts = [
            post for post in store.posts_db 
            if post and post.get('file_metadata', {}).get('file_id') == request.file_id
        ]
        
        post_count = len(related_posts)
        reply_count = sum(len(post.get('replies', [])) for post in related_posts if post)
        
        # Store summary with client-specific key
        summary_id_counter += 1
        summary_key = f"{client_id}_{request.file_id}"
        summary_data = {
            "id": summary_id_counter,
            "file_id": request.file_id,
            "content": result['content'],
            "next_steps": result.get('next_steps', []),  # Store next steps as list
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "post_count": post_count,
            "reply_count": reply_count
        }
        
        summaries_db[summary_key] = summary_data
        
        logger.info(f"Summary created: {summary_id_counter} for file {request.file_id}")
        logger.info(f"Next steps cards: {len(result.get('next_steps', []))}")
        
        return SummaryResponse(
            success=True,
            file_id=request.file_id,
            content=result['content'],
            next_steps=result.get('next_steps', []),  # Return next steps as list
            created_at=summary_data['created_at'],
            post_count=post_count,
            reply_count=reply_count
        )
        
    except Exception as e:
        logger.error(f"Failed to create summary: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
async def list_summaries():
    """
    List all generated summaries
    
    Returns:
        List of summary metadata
    """
    logger.info(f"GET all summaries (total: {len(summaries_db)})")
    
    return {
        "success": True,
        "count": len(summaries_db),
        "summaries": [
            {
                "id": data['id'],
                "file_id": data['file_id'],
                "created_at": data['created_at'],
                "post_count": data['post_count'],
                "reply_count": data['reply_count']
            }
            for data in summaries_db.values()
        ]
    }
