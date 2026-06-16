"""
Feed-related API endpoints

Now supports client-isolated data stores.
Each client (browser tab) has its own independent feed.
"""
from fastapi import APIRouter, HTTPException, Form, File, UploadFile, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from typing import Optional, List
from datetime import datetime
import asyncio
import math
import json

from models.schemas import Post, Reply
from services import file_service
from services.orchestration_service import SummaryTrigger, extract_data_scope, build_scope_sql_condition
from services.websocket_manager import ws_manager
from services.client_store import get_client_store, client_store_manager
from services.next_step_routing import infer_next_step_target_agent_role, normalize_target_agent_role
from config import settings
from prompts import (
    build_contextual_next_steps_prompt,
    build_segment_next_steps_prompt,
    build_like_recommendations_prompt,
)
from context_management import ContextBlock, assemble_context, truncate_text
from utils.logger import logger

router = APIRouter(prefix="/api", tags=["feed"])


# ==================== Settings Endpoints ====================

@router.get("/settings")
async def get_settings(client_id: str = Query(...)):
    """Get client settings (language preference)"""
    store = get_client_store(client_id)
    return {"language": store.language}


@router.put("/settings")
async def update_settings(client_id: str = Query(...), request: dict = None):
    """Update client settings"""
    store = get_client_store(client_id)
    if request and "language" in request:
        store.language = request["language"]
        logger.info(f"Language set to '{store.language}' for client {client_id[:20]}...")
    return {"language": store.language}


@router.get("/settings/export")
async def export_session_data(client_id: str = Query(...)):
    """Export the current session conversation as JSON, preserving post/reply structure."""
    store = get_client_store(client_id)

    exported_posts = sanitize_floats(store.get_all_posts())

    payload = {
        "schema_version": 1,
        "exported_at": datetime.now().isoformat(),
        "session": {
            "client_id": store.client_id,
            "created_at": store.created_at.isoformat(),
            "last_accessed": store.last_accessed.isoformat(),
            "language": store.language,
        },
        "agents": sanitize_floats(store.get_all_agents()),
        "files": sanitize_floats(list(store.files.values())),
        "conversation": {
            "post_count": len(exported_posts),
            "reply_count": sum(len(post.get("replies", [])) for post in exported_posts),
            "posts": exported_posts,
        },
    }

    filename = f"treadstone-session-{client_id[:12]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@router.post("/settings/import")
async def import_session_data(client_id: str = Query(...), file: UploadFile = File(...)):
    """Import a previously exported session JSON to replace the current session."""
    store = get_client_store(client_id)

    try:
        content = await file.read()
        data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {e}")

    # Support both current export format and older flat session exports.
    posts = None
    if "conversation" in data and "posts" in data["conversation"]:
        posts = data["conversation"]["posts"]
    elif "posts" in data:
        posts = data["posts"]
    else:
        raise HTTPException(status_code=400, detail="No posts found in JSON. Expected 'conversation.posts' or 'posts' key.")

    store.posts_db = posts

    max_post_id = max((p.get("id", 0) for p in posts), default=0)
    max_reply_id = max(
        (r.get("id", 0) for p in posts for r in p.get("replies", [])),
        default=0
    )
    store.post_id_counter = max_post_id + 1
    store.reply_id_counter = max_reply_id + 1

    if "session" in data and "language" in data["session"]:
        store.language = data["session"]["language"]

    # Register file metadata from posts
    for post in posts:
        fm = post.get("file_metadata")
        if fm and fm.get("file_id"):
            fm_with_client = {**fm, "client_id": store.client_id}
            store.files[fm["file_id"]] = fm_with_client

    # Register top-level files if present
    for fmeta in data.get("files", []):
        if fmeta.get("file_id"):
            fmeta_with_client = {**fmeta, "client_id": store.client_id}
            store.files[fmeta["file_id"]] = fmeta_with_client

    total_replies = sum(len(p.get("replies", [])) for p in posts)
    logger.info(f"Imported session for {client_id[:20]}...: {len(posts)} posts, {total_replies} replies")

    return {
        "success": True,
        "posts_imported": len(posts),
        "replies_imported": total_replies
    }


def sanitize_floats(obj):
    """Recursively replace NaN/Infinity float values with None for JSON compliance."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: sanitize_floats(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_floats(item) for item in obj]
    return obj

# Summary trigger is now per-client (stored in ClientStore)
# Global summary_trigger kept for backwards compatibility during transition
summary_trigger = SummaryTrigger()


# ============ COMMON AUTO-TAGGING FUNCTION ============
async def auto_tag_reply_content(reply: dict, post: dict, client_id: str = None):
    """
    Auto-tag a reply based on its content and FULL feed context.
    Also infers connections to previous tagged items.
    Works for both user and agent replies.
    Sends WebSocket update when tags are assigned.
    """
    try:
        from agents_sdk import auto_tag_content, infer_connections
        
        # Build FULL feed context for accurate tagging
        feed_context = []
        if client_id:
            store = get_client_store(client_id)
            for p in store.posts_db:
                if not p:
                    continue
                # Add post to context
                feed_context.append({
                    "type": "post",
                    "author": p.get("author", "Unknown"),
                    "content": p.get("content", ""),
                    "tags": p.get("tags", []),
                    "created_at": p.get("created_at", "")
                })
                # Add replies to context
                for r in p.get("replies", []):
                    if r.get("id") != reply.get("id"):  # Exclude current reply
                        feed_context.append({
                            "type": "reply",
                            "author": r.get("author", "Unknown"),
                            "content": r.get("content", ""),
                            "tags": r.get("tags", []),
                            "created_at": r.get("created_at", "")
                        })
            # Sort by created_at
            feed_context.sort(key=lambda x: x.get("created_at", ""))
        
        content = reply.get('content', '')
        auto_tags = await auto_tag_content(content, feed_context=feed_context)
        reply["tags"] = auto_tags
        
        if auto_tags:
            logger.debug(f"Auto-tagged reply {reply.get('id')}: {auto_tags}")
            # Send WebSocket update so frontend can update the tags in real-time
            post_id = post.get('id') or reply.get('post_id')
            reply_id = reply.get('id')
            if post_id and reply_id:
                await ws_manager.emit_reply_tags_updated(post_id, reply_id, auto_tags)
        
        # Always try to infer connections (even without tags)
        if client_id:
            try:
                store = get_client_store(client_id)
                # Build list of ALL previous items (not just tagged ones)
                previous_items = []
                for p in store.posts_db:
                    if p:
                        previous_items.append({
                            "id": f"post_{p.get('id')}",
                            "type": "post",
                            "content": p.get("content", "")[:200],
                            "tags": p.get("tags", [])
                        })
                    for r in p.get("replies", []):
                        if r.get("id") != reply.get("id"):
                            previous_items.append({
                                "id": f"reply_{p.get('id')}_{r.get('id')}",
                                "type": "reply", 
                                "content": r.get("content", "")[:200],
                                "tags": r.get("tags", [])
                            })
                
                logger.debug(f"Inferring connections for reply {reply.get('id')}: {len(previous_items)} previous items")
                
                if previous_items and len(previous_items) > 0:
                    connections = await infer_connections(
                        content=content,
                        item_id=f"reply_{reply.get('id')}",
                        item_type="reply",
                        tags=auto_tags or [],
                        previous_items=previous_items
                    )
                    reply["connections"] = connections
                    logger.debug(f"infer_connections returned: {len(connections) if connections else 0} connections")
                    if connections:
                        logger.debug(f"Reply {reply.get('id')} connected to {len(connections)} items")
                else:
                    logger.debug(f"No previous items to connect to")
            except Exception as conn_err:
                logger.warning(f"Connection inference failed: {conn_err}")
    except Exception as e:
        logger.warning(f"Auto-tagging failed for reply {reply.get('id')}: {e}")


@router.get("/feed")
async def get_feed(client_id: str = Query(..., description="Client ID for session isolation")):
    """Get list of posts (oldest first, latest at bottom) for this client"""
    store = get_client_store(client_id)
    sorted_posts = store.get_all_posts()
    return {"posts": sanitize_floats(sorted_posts)}


# ============ SINGLE POST API (for API fallback) ============
@router.get("/post/{post_id}")
async def get_post(
    post_id: int,
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """
    Get a single post by ID with all replies.
    Used as API fallback when WebSocket message might be incomplete.
    
    Args:
        post_id: Post ID
        client_id: Client ID for session isolation
    
    Returns:
        Complete post object with all replies and visualizations
    """
    store = get_client_store(client_id)
    post = store.get_post(post_id)
    
    if not post:
        raise HTTPException(status_code=404, detail=f"Post {post_id} not found")
    
    return {"post": sanitize_floats(post)}


# ============ PENDING MESSAGES API ============
@router.get("/pending-messages")
async def get_pending_messages(
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """
    Get pending messages for a client.
    Used when WebSocket reconnects or as explicit polling.
    
    This is an alternative to WebSocket auto-delivery for cases where
    the client wants to explicitly request pending messages.
    
    Args:
        client_id: Client ID for session isolation
    
    Returns:
        List of pending messages
    """
    store = get_client_store(client_id)
    messages = store.get_pending_messages()
    
    return {
        "messages": messages,
        "count": len(messages)
    }


@router.get("/next-steps/{file_id}")
async def get_next_steps(
    file_id: str, 
    client_id: str = Query(..., description="Client ID for session isolation"),
    exclude_ids: str = ""
):
    """
    Get next steps extracted from proactive agent posts.
    This works without requiring a full summary to be generated.
    
    Args:
        file_id: File ID to get next steps for
        client_id: Client ID for session isolation
        exclude_ids: Comma-separated list of question IDs to exclude (already clicked)
    
    Returns:
        List of next steps with id, icon, title, description, and question
    """
    import re
    
    store = get_client_store(client_id)

    # Parse excluded IDs
    excluded_set = set()
    if exclude_ids:
        try:
            excluded_set = {int(id_str.strip()) for id_str in exclude_ids.split(",") if id_str.strip()}
        except ValueError:
            logger.warning(f"Invalid exclude_ids format: {exclude_ids}")
    
    # Find all proactive posts (from Data Scout Agent) for this file
    # Use author_role instead of author name (supports custom names)
    proactive_posts = [
        p for p in store.posts_db 
        if p.get("author_role") == "scanner" 
        and p.get("file_metadata", {}).get("file_id") == file_id
    ]
    
    if not proactive_posts:
        return {"next_steps": [], "source": "no_proactive_posts"}
    
    # Extract questions from proactive posts
    all_questions = []
    question_id = 1
    
    # Icons for variety
    icons = ["📊", "🌍", "💥", "📈", "🔍", "⚡", "🎯", "📋"]
    
    for post in proactive_posts:
        content = post.get("content", "")
        
        # Look for numbered questions patterns:
        # 1 question text
        # 2 question text
        # - Question: text
        # 1. Question text
        patterns = [
            r'[1-5]️⃣\s+(.+?)(?=\n[1-5]️⃣|\n\n|$)',  # Emoji numbered
            r'\d\.\s+(.+?)(?=\n\d\.|\n\n|$)',  # Regular numbered
            r'[-•]\s*(.+?\?)',  # Bullet with question mark
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
            for match in matches:
                question = match.strip()
                if len(question) > 20 and "?" in question:
                    # Extract a short title from the question
                    title = question[:50].split("?")[0] + "?" if "?" in question[:50] else question[:40] + "..."
                    
                    all_questions.append({
                        "id": question_id,
                        "icon": icons[(question_id - 1) % len(icons)],
                        "title": title,
                        "description": question[:100] + "..." if len(question) > 100 else question,
                        "question": question,
                        "target_agent_role": infer_next_step_target_agent_role(question),
                    })
                    question_id += 1
    
    # Filter out excluded questions
    next_steps = [q for q in all_questions if q["id"] not in excluded_set]
    
    # Limit to 5 steps
    next_steps = next_steps[:5]
    
    logger.debug(f"Extracted {len(next_steps)} next steps (excluded {len(excluded_set)} IDs) for file {file_id}")
    
    return {"next_steps": next_steps, "source": "proactive_posts"}


@router.post("/next-steps/{file_id}/contextual")
async def get_contextual_next_steps(
    file_id: str, 
    request: dict,
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """
    Generate contextual next steps based on recent conversation.
    Uses an internal generator to suggest relevant follow-up questions.
    
    Args:
        file_id: File ID
        client_id: Client ID for session isolation
        request: {
            "exclude_ids": [1, 2, 3],  # IDs to exclude
            "count": 3  # Number of questions to generate
        }
    
    Returns:
        List of contextual next steps
    """
    from agents_sdk import next_step_generator, uploaded_datasets
    from agents import Runner
    import random
    
    store = get_client_store(client_id)

    exclude_ids = set(request.get("exclude_ids", []))
    count = request.get("count", 3)
    
    # Get recent posts for context
    recent_posts = [
        p for p in store.posts_db[-10:]  # Last 10 posts
        if p.get("file_metadata", {}).get("file_id") == file_id
    ]
    
    if not recent_posts:
        logger.warning(f"No recent posts found for file {file_id} (client: {client_id[:20]}...)")
        return {"next_steps": [], "source": "no_context"}
    
    # Get data schema information
    data_schema_info = ""
    if file_id in uploaded_datasets:
        dataset = uploaded_datasets[file_id]
        columns = dataset.get("columns", [])
        rows = dataset.get("rows", 0)
        data_schema_info = f"""
Dataset Schema:
- Total rows: {rows:,}
- Columns ({len(columns)}): {', '.join(columns[:20])}{'...' if len(columns) > 20 else ''}
"""
    
    context_blocks = []
    for post in recent_posts[-5:]:  # Last 5 posts
        lines = [
            f"- {post.get('author')}: {truncate_text(post.get('content', ''), 80, preserve='head')}"
        ]
        
        for reply in (post.get('replies', []))[-3:]:  # Last 3 replies per post
            lines.append(
                f"  → {reply.get('author')}: {truncate_text(reply.get('content', ''), 60, preserve='head')}"
            )
        context_blocks.append(ContextBlock(
            name=f"post_{post.get('id', 'unknown')}",
            content="\n".join(lines),
            token_budget=260,
            preserve="tail"
        ))
    
    conversation_context = assemble_context(context_blocks, token_budget=1200)
    
    # Get language preference (Auto-detect from conversation context)
    from agents_sdk import resolve_language
    lang = resolve_language(client_id=client_id, message=conversation_context)
    
    # Prompt for Agent to generate contextual questions
    prompt = build_contextual_next_steps_prompt(
        language=lang,
        count=count,
        data_schema_info=data_schema_info,
        conversation_context=conversation_context,
    )
    
    try:
        logger.debug(f"Generating {count} contextual questions for file {file_id}")
        
        # Run the internal generator to generate questions
        result = await Runner.run(
            starting_agent=next_step_generator,
            input=prompt,
            max_turns=5
        )
        
        # Parse questions from Agent response
        response_text = result.final_output or ""
        
        # Extract questions
        import re
        questions = []
        question_patterns = [
            r'Q:\s*(.+?)(?=\n|$)',  # Q: format
            r'\d+\.\s+(.+?\?)(?=\n|$)',  # 1. format
            r'[-•]\s*(.+?\?)(?=\n|$)',  # Bullet format
        ]
        
        for pattern in question_patterns:
            matches = re.findall(pattern, response_text, re.MULTILINE)
            for match in matches:
                question = match.strip()
                if len(question) > 20 and "?" in question:
                    questions.append(question)
        
        # Remove duplicates, strip backticks, and limit
        from agents_sdk import _strip_backticks
        questions = [_strip_backticks(q) for q in dict.fromkeys(questions)][:count]
        
        if not questions:
            logger.warning("Next-step generator did not return valid questions, falling back to extraction")
            # Fallback: use existing extraction logic
            return await get_next_steps(file_id, client_id, ",".join(map(str, exclude_ids)))
        
        # Format as next steps
        icons = ["📊", "🌍", "💥", "📈", "🔍", "⚡", "🎯", "📋"]
        next_steps = []
        
        for i, question in enumerate(questions):
            # Generate unique ID (use timestamp + random to avoid conflicts)
            step_id = int(f"{int(datetime.now().timestamp() * 1000)}{random.randint(10, 99)}")
            
            title = question[:50].split("?")[0] + "?" if "?" in question[:50] else question[:40] + "..."
            
            next_steps.append({
                "id": step_id,
                "icon": icons[i % len(icons)],
                "title": title,
                "description": question[:100] + "..." if len(question) > 100 else question,
                "question": question,
                "target_agent_role": infer_next_step_target_agent_role(question),
            })
        
        logger.debug(f"Generated {len(next_steps)} contextual questions")
        return {"next_steps": next_steps, "source": "agent_contextual"}
        
    except Exception as e:
        logger.error(f"Error generating contextual questions: {str(e)}", exc_info=True)
        # Fallback to regular extraction
        return await get_next_steps(file_id, client_id, ",".join(map(str, exclude_ids)))


@router.post("/next-steps/{file_id}/segment")
async def get_segment_next_steps(
    file_id: str, 
    request: dict,
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """
    Generate next steps based on a specific segment of posts in the feed.
    Each segment gets context-specific recommendations.
    
    Args:
        file_id: File ID
        client_id: Client ID for session isolation
        request: {
            "post_ids": [1, 2, 3],  # Post IDs in this segment
            "segment_index": 0,  # Which segment (0, 1, 2...)
            "count": 3  # Number of questions to generate
        }
    
    Returns:
        List of segment-specific next steps
    """
    from agents_sdk import next_step_generator, uploaded_datasets
    from agents import Runner
    import random
    
    store = get_client_store(client_id)
    
    post_ids = set(request.get("post_ids", []))
    segment_index = request.get("segment_index", 0)
    count = request.get("count", 3)
    
    # Get posts in this segment
    segment_posts = [p for p in store.posts_db if p.get("id") in post_ids]
    
    if not segment_posts:
        logger.warning(f"No posts found for segment {segment_index}")
        return {"next_steps": [], "source": "no_segment_posts"}
    
    # Get data schema information
    data_schema_info = ""
    if file_id in uploaded_datasets:
        dataset = uploaded_datasets[file_id]
        columns = dataset.get("columns", [])
        rows = dataset.get("rows", 0)
        data_schema_info = f"""
Dataset Schema:
- Total rows: {rows:,}
- Columns ({len(columns)}): {', '.join(columns[:20])}{'...' if len(columns) > 20 else ''}
"""
    
    context_blocks = []
    for post in segment_posts:
        author = post.get('author', 'Unknown')
        lines = [
            f"- {author}: {truncate_text(post.get('content', ''), 110, preserve='head')}"
        ]
        
        for reply in (post.get('replies', []))[-2:]:
            reply_author = reply.get('author', 'Agent')
            reply_content = truncate_text(reply.get('content', ''), 80, preserve='head')
            lines.append(f"  → {reply_author}: {reply_content}")
        context_blocks.append(ContextBlock(
            name=f"segment_post_{post.get('id', 'unknown')}",
            content="\n".join(lines),
            token_budget=300,
            preserve="tail"
        ))
    
    conversation_context = assemble_context(context_blocks, token_budget=1300)
    
    # Get language preference (Auto-detect from conversation context)
    from agents_sdk import resolve_language
    seg_lang = resolve_language(client_id=client_id, message=conversation_context)
    
    # Prompt for segment-specific questions
    prompt = build_segment_next_steps_prompt(
        language=seg_lang,
        count=count,
        data_schema_info=data_schema_info,
        conversation_context=conversation_context,
    )
    
    try:
        logger.debug(f"Generating {count} segment-specific questions (segment {segment_index})")
        
        result = await Runner.run(
            starting_agent=next_step_generator,
            input=prompt,
            max_turns=5
        )
        
        response_text = result.final_output or ""
        
        # Extract questions
        import re
        questions = []
        question_patterns = [
            r'Q:\s*(.+?)(?=\n|$)',
            r'\d+\.\s+(.+?\?)(?=\n|$)',
            r'[-•]\s*(.+?\?)(?=\n|$)',
        ]
        
        for pattern in question_patterns:
            matches = re.findall(pattern, response_text, re.MULTILINE)
            for match in matches:
                question = match.strip()
                if len(question) > 20 and "?" in question:
                    questions.append(question)
        
        from agents_sdk import _strip_backticks
        questions = [_strip_backticks(q) for q in dict.fromkeys(questions)][:count]
        
        if not questions:
            logger.warning(f"No questions generated for segment {segment_index}")
            return {"next_steps": [], "source": "no_questions"}
        
        # Format as next steps with segment-unique IDs
        icons = ["📊", "🌍", "💥", "📈", "🔍", "⚡", "🎯", "📋"]
        next_steps = []
        
        for i, question in enumerate(questions):
            # Include segment index in ID to ensure uniqueness across segments
            step_id = int(f"{segment_index}{int(datetime.now().timestamp() * 100)}{random.randint(10, 99)}")
            
            title = question[:50].split("?")[0] + "?" if "?" in question[:50] else question[:40] + "..."
            
            next_steps.append({
                "id": step_id,
                "icon": icons[(i + segment_index) % len(icons)],
                "title": title,
                "description": question[:100] + "..." if len(question) > 100 else question,
                "question": question,
                "target_agent_role": infer_next_step_target_agent_role(question),
                "segment_index": segment_index
            })
        
        logger.debug(f"Generated {len(next_steps)} segment-specific questions for segment {segment_index}")
        return {"next_steps": next_steps, "source": "agent_segment", "segment_index": segment_index}
        
    except Exception as e:
        logger.error(f"Error generating segment questions: {str(e)}", exc_info=True)
        return {"next_steps": [], "source": "error"}


@router.get("/stats")
async def get_system_stats():
    return client_store_manager.get_all_stats()

@router.post("/post")
async def create_post(
    client_id: str = Form(...),  # Required for client isolation
    author: str = Form(...),
    author_type: str = Form(...),
    content: str = Form(...),
    author_role: Optional[str] = Form(None),
    visualization: Optional[str] = Form(None),
    hitl_options: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    references_post_id: Optional[int] = Form(None),
    file_id: Optional[str] = Form(None),  # For proactive posts: pass file_id directly
    target_agent_role: Optional[str] = Form(None)
):
    """
    Create a new post
    
    Args:
        client_id: Client ID for session isolation (required)
        author: Author name
        author_type: "user" or "agent"
        content: Post content
        author_role: Optional agent role
        visualization: Optional visualization JSON string
        hitl_options: Optional HITL options JSON string
        file: Optional file attachment (CSV or TXT)
        
    Returns:
        Created post with metadata
    """
    store = get_client_store(client_id)
    target_agent_role = normalize_target_agent_role(target_agent_role)
    
    # Handle file upload or file_id reference
    file_metadata = None
    if file:
        # Case 1: New file upload (from user)
        try:
            uploaded_file = await file_service.upload_file(file, client_id=client_id)
            file_metadata = uploaded_file.dict()
            logger.info(f"File uploaded: {file_metadata['original_filename']} for client {client_id[:20]}...")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")
    elif file_id:
        # Case 2: Reference existing file (from proactive agent)
        try:
            file_metadata = file_service.get_file(file_id)
            if file_metadata:
                logger.debug(f"Referenced existing file: {file_metadata.get('original_filename')}")
            else:
                logger.warning(f"file_id {file_id} not found")
        except Exception as e:
            logger.warning(f"Failed to get file metadata: {str(e)}")
    
    # Parse visualization and HITL options
    import json
    viz_data = None
    hitl_list = None
    
    if visualization:
        try:
            viz_data = json.loads(visualization)
        except:
            pass
    
    if hitl_options:
        try:
            hitl_list = json.loads(hitl_options)
        except:
            pass
    
    # Create post
    created_post_id = store.get_next_post_id()
    new_post = {
        "id": created_post_id,
        "author": author,
        "author_type": author_type,
        "author_role": author_role,
        "content": content,
        "created_at": datetime.now().isoformat(),
        "likes": 0,
        "replies": [],
        "visualization": viz_data,
        "hitl_options": hitl_list,
        "file_metadata": file_metadata,
        "references_post_id": references_post_id,
        "target_agent_role": target_agent_role,
        "data_scope": None,  # Will be extracted from user message
        "tags": [],  # Auto-tagged + user-added tags
        "client_id": client_id  # Track which client created this post
    }
    
    store.add_post(new_post)
    
    # Auto-tag user post content (background) with FULL feed context
    async def auto_tag_post():
        try:
            from agents_sdk import auto_tag_content
            # Build FULL feed context for accurate tagging
            feed_context = []
            for p in store.posts_db:
                if not p or p.get("id") == created_post_id:
                    continue
                feed_context.append({
                    "type": "post",
                    "author": p.get("author", "Unknown"),
                    "content": p.get("content", ""),
                    "tags": p.get("tags", []),
                    "created_at": p.get("created_at", "")
                })
                for r in p.get("replies", []):
                    feed_context.append({
                        "type": "reply",
                        "author": r.get("author", "Unknown"),
                        "content": r.get("content", ""),
                        "tags": r.get("tags", []),
                        "created_at": r.get("created_at", "")
                    })
            feed_context.sort(key=lambda x: x.get("created_at", ""))
            
            auto_tags = await auto_tag_content(content, feed_context=feed_context)
            new_post["tags"] = auto_tags
            logger.debug(f"Auto-tagged post {created_post_id} (client: {client_id[:20]}...): {auto_tags}")
        except Exception as e:
            logger.warning(f"Auto-tagging failed for post {created_post_id}: {e}")
    
    asyncio.create_task(auto_tag_post())
    
    # ============ FIND CONTEXT FILE_ID ============
    # If no file attached, find the most recent file_id from previous posts
    context_file_id = None
    context_file_path = None
    
    # Image file types that should NOT be used as analysis context
    image_extensions = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
    
    if file_metadata and file_metadata.get('file_id'):
        # User uploaded a file with this post
        file_type = file_metadata.get('file_type', '').lower()
        
        if file_type not in image_extensions:
            # Data file (CSV, TXT) - use as context
            context_file_id = file_metadata.get('file_id')
            context_file_path = file_metadata.get('file_path')
        else:
            # Image file - don't use as context, look for previous data file
            logger.debug(f"Image file uploaded, looking for previous data file for context")
            for post in reversed(store.posts_db[:-1]):
                if post and post.get('file_metadata'):
                    prev_file_type = post['file_metadata'].get('file_type', '').lower()
                    if prev_file_type not in image_extensions and post['file_metadata'].get('file_id'):
                        context_file_id = post['file_metadata'].get('file_id')
                        context_file_path = post['file_metadata'].get('file_path')
                        logger.debug(f"Using previous data file for context: {context_file_id}")
                        break
    else:
        # No file attached - look for most recent DATA file in feed (skip images)
        for post in reversed(store.posts_db[:-1]):  # Exclude the just-created post
            if post and post.get('file_metadata') and post['file_metadata'].get('file_id'):
                file_type = post['file_metadata'].get('file_type', '').lower()
                if file_type not in image_extensions:
                    context_file_id = post['file_metadata'].get('file_id')
                    context_file_path = post['file_metadata'].get('file_path')
                    logger.debug(f"Found context file_id from previous post: {context_file_id}")
                    
                    #  UPDATE: Add file_metadata to the post for future references
                    # This ensures replies can find the file_id
                    context_file_info = file_service.get_file(context_file_id)
                    if context_file_info:
                        # Update the just-created post with file_metadata
                        store.posts_db[-1]['file_metadata'] = context_file_info
                        logger.debug(f"Updated POST {created_post_id} with file_metadata from context")
                    break
    
    # ============ AUTO SUMMARY TRIGGER ============
    # Check if summary should be auto-generated
    if context_file_id:
        check_and_trigger_summary_background(context_file_id, client_id)
    
    # ============ USER POST → AGENT ANALYSIS ============
    if author_type == "user":
        if context_file_id and context_file_path:
            logger.info(f"User post detected: POST {created_post_id} (client: {client_id[:20]}...)")
            logger.info(f"Content: {content[:100]}...")
            logger.info(f"Context file_id: {context_file_id}")
            
            # Check if current post has an attached image (separate from data context)
            attached_image_path = None
            if file_metadata:
                attached_file_type = file_metadata.get('file_type', '').lower()
                if attached_file_type in image_extensions:
                    attached_image_path = file_metadata.get('file_path')
                    new_post["image_file_path"] = attached_image_path
                    logger.info(f"Image attached: {attached_image_path}")
            
            asyncio.create_task(
                add_agent_analysis_to_post_background(
                    post_id=created_post_id,
                    user_message=content,
                    file_id=context_file_id,
                    file_path=context_file_path,
                    image_file_path=attached_image_path,
                    client_id=client_id,
                    target_agent_role=target_agent_role,
                )
            )
            logger.info(f"Agent analysis scheduled for user POST {created_post_id}")
    
    # ============ AUTO AGENT COLLABORATION FOR PROACTIVE POSTS ============
    # If this is a proactive scanner post with questions, schedule auto-collaboration
    if (author_role == "scanner" and 
        author_type == "agent" and 
        file_metadata and 
        any(q in content for q in ["1️⃣", "2️⃣", "3️⃣"])):
        
        
        logger.info(f"Scheduling auto-collaboration for initial proactive POST {created_post_id}")
        
        file_path = file_metadata.get('file_path')
        file_id_for_collab = file_metadata.get('file_id')
        
        if file_path and file_id_for_collab:
            asyncio.create_task(
                add_auto_collaboration_replies_background(
                    proactive_post_id=created_post_id,
                    proactive_content=content,
                    file_id=file_id_for_collab,
                    file_path=file_path,
                    client_id=client_id
                )
            )
            logger.info(f"Auto-collaboration scheduled for initial proactive POST {created_post_id}")
    
    # ============ DISCUSSION TRIGGER FOR ALL DATA SCOUT POSTS ============
    elif (author_role == "scanner" and 
          author_type == "agent" and 
          file_metadata):
        
        file_id_for_disc = file_metadata.get('file_id')
        
        if file_id_for_disc:
            # Schedule standalone discussion trigger
            async def trigger_discussion_for_scout_post():
                try:
                    from agents_sdk import trigger_agent_discussion
                    
                    logger.info(f"Triggering discussion for Data Scout POST {created_post_id}")
                    
                    # 실시간 reply 처리 콜백
                    async def on_reply(discussion_reply):
                        disc_reply = {
                            "id": store.get_next_reply_id(),
                            "post_id": created_post_id,
                            "author": discussion_reply["agent_name"],
                            "author_type": "agent",
                            "author_role": discussion_reply["agent_role"],
                            "content": discussion_reply["content"],
                            "created_at": datetime.now().isoformat(),
                            "likes": 0,
                            "visualization": discussion_reply.get("visualization"),
                            "is_discussion": True,
                            "tags": []
                        }
                        new_post["replies"].append(disc_reply)
                        logger.info(f"Discussion reply added to Data Scout POST from {discussion_reply['agent_name']}")
                        await ws_manager.emit_new_reply(created_post_id, disc_reply, client_id)
                        # Auto-tag discussion reply
                        asyncio.create_task(auto_tag_reply_content(disc_reply, new_post, client_id))
                    
                    await trigger_agent_discussion(
                        previous_response=content,
                        responding_agent="scanner",
                        file_id=file_id_for_disc,
                        original_context=content,
                        threshold=9,
                        post_id=created_post_id,
                        on_reply_generated=on_reply,  # 실시간 콜백
                        client_id=client_id  # NEW: pass client_id for isolation
                    )
                except Exception as e:
                    logger.warning(f"Discussion trigger failed for Data Scout post: {e}")
            
            asyncio.create_task(trigger_discussion_for_scout_post())
            logger.info(f"Discussion trigger scheduled for Data Scout POST {created_post_id}")
    
    return new_post


def _build_post_context(post: dict, max_replies: int = 10, token_budget: int = 2200) -> str:
    """
    Build a reference context string from POST content and recent replies.
    Limits context size to prevent exceeding LLM context window.
    
    Args:
        post: The post dict with replies
        max_replies: Maximum number of recent replies to include (default 10)
        token_budget: Approximate token budget for the context
    """
    blocks = []
    
    # Add original POST content
    post_content = post.get("content", "")
    post_author = post.get("author", "User")
    if post_content:
        blocks.append(ContextBlock(
            name="original_post",
            content=f"=== Original POST by {post_author} ===\n{post_content}",
            token_budget=220,
            preserve="head"
        ))
    
    # Add only recent replies (last N) to prevent context overflow
    replies = post.get("replies", [])
    recent_replies = replies[-max_replies:] if len(replies) > max_replies else replies
    
    if recent_replies:
        if len(replies) > max_replies:
            heading = f"=== Previous Discussion (showing last {max_replies} of {len(replies)} replies) ==="
        else:
            heading = "=== Previous Discussion ==="
        discussion_lines = [heading]
        
        for reply in recent_replies:
            author = reply.get("author", "Unknown")
            author_type = reply.get("author_type", "")
            content = truncate_text(reply.get("content", ""), 180, preserve="middle")
            
            if author_type == "agent":
                discussion_lines.append(f"[{author}]: {content}")
            else:
                discussion_lines.append(f"[User - {author}]: {content}")
            discussion_lines.append("")
        blocks.append(ContextBlock(
            name="recent_discussion",
            content="\n".join(discussion_lines),
            preserve="tail"
        ))
    
    context = assemble_context(blocks, token_budget=token_budget)
    
    logger.debug(f"Built reference context for POST {post.get('id')}: {len(recent_replies)}/{len(replies)} replies, {len(context)} chars")
    return context


async def add_agent_analysis_to_post_background(
    post_id: int,
    user_message: str,
    file_id: str,
    file_path: str,
    image_file_path: str = None,
    client_id: str = None,
    target_agent_role: Optional[str] = None,
):
    """
    Background task to add agent analysis as replies to a user's post.
    Uses Discussion-based agent selection (no Triage agent).
    
    Flow:
    1. All agents evaluate the question in parallel
    2. Highest scoring agent responds (Round 1 - mandatory)
    3. Remaining agents re-evaluate and respond if score >= threshold (Round 2+)
    """
    from agents_sdk import (
        get_agent_by_role,
        get_agent_display_name,
        parse_mentions,
        run_agent_analysis,
        trigger_agent_discussion,
    )
    import random
    
    if not client_id:
        logger.error(f"client_id is required for agent analysis")
        return
    
    store = get_client_store(client_id)
    post = store.get_post(post_id)
    if not post:
        logger.error(f"Post {post_id} not found for agent analysis (client: {client_id[:20]}...)")
        return
    
    try:
        # ============ EXTRACT DATA SCOPE ============
        data_scope = post.get("data_scope")
        
        if data_scope is None:
            # Try to inherit scope from referenced post (if this is a follow-up)
            if post.get("references_post_id"):
                parent_post = store.get_post(post["references_post_id"])
                if parent_post and parent_post.get("data_scope"):
                    data_scope = parent_post["data_scope"]
                    logger.debug(f"Inherited data_scope from parent POST {post['references_post_id']}: {data_scope}")
            
            # If still no scope, try to extract from user message
            if data_scope is None:
                file_info_for_scope = file_service.get_file(file_id)
                if file_info_for_scope and file_info_for_scope.get("columns"):
                    columns = file_info_for_scope["columns"]
                    data_scope = await extract_data_scope(user_message, columns)
                    
                    if data_scope:
                        post["data_scope"] = data_scope
                        logger.debug(f"Extracted and saved data_scope for POST {post_id}: {data_scope}")
        
        if data_scope:
            logger.debug(f"Using data_scope for POST {post_id}: {data_scope}")
        
        # ============ @MENTION PARSING ============
        mentioned_role, mentioned_agent_id, cleaned_message = parse_mentions(user_message, client_id)
        target_agent_role = normalize_target_agent_role(target_agent_role)
        if not mentioned_role and target_agent_role:
            mentioned_role = target_agent_role
            mentioned_agent_id = None
            cleaned_message = user_message
            logger.info(f"Target agent role supplied for POST {post_id}: {mentioned_role}")
        
        # Natural delay before agents respond
        delay = random.uniform(2.0, 4.0)
        logger.debug(f"Agents evaluating for {delay:.1f}s before replying to POST {post_id}...")
        await asyncio.sleep(delay)
        
        # ============ @MENTION: Direct routing to specific agent ============
        if mentioned_role:
            # Determine display name - use specific agent if mentioned by name
            if mentioned_agent_id:
                # User mentioned a specific agent by name.
                specific_agent = store.agent_registry.get(mentioned_agent_id)
                display_name = specific_agent["name"] if specific_agent else get_agent_display_name(mentioned_role, client_id)
                logger.info(f"@Mention detected: specific agent '{display_name}' (id={mentioned_agent_id}) - routing directly")
            else:
                # User used a role keyword.
                display_name = get_agent_display_name(mentioned_role, client_id)
                logger.info(f"@Mention detected: role {mentioned_role} -> '{display_name}' - routing directly")
            
            agent = get_agent_by_role(mentioned_role)
            
            if agent:
                # ============ BUILD REFERENCE CONTEXT FROM PREVIOUS REPLIES ============
                # Include POST content and all previous replies so agent knows the conversation
                reference_context = _build_post_context(post)
                
                # Run analysis with the mentioned agent
                result = await run_agent_analysis(
                    message=cleaned_message,
                    file_path=file_path,
                    file_id=file_id,
                    agent=agent,
                    post_id=post_id,
                    data_scope=data_scope,
                    client_id=client_id,
                    display_name=display_name,  # Pass custom name for typing indicator
                    agent_role=mentioned_role,  # Pass role for status row detection
                    reference_context=reference_context,  # Include previous conversation
                    image_file_path=image_file_path  # Pass image for Vision API
                )
                
                agent_reply = {
                    "id": store.get_next_reply_id(),
                    "post_id": post_id,
                    "author": display_name,
                    "author_type": "agent",
                    "author_role": mentioned_role,
                    "content": result.get("content", ""),
                    "created_at": datetime.now().isoformat(),
                    "likes": 0,
                    "visualization": result.get("visualization"),
                    "tags": []
                }
                
                post["replies"].append(agent_reply)
                await ws_manager.emit_new_reply(post_id, agent_reply, client_id)
                logger.info(f"@Mentioned agent {display_name} replied to POST {post_id}")
                # Auto-tag the mentioned agent reply
                asyncio.create_task(auto_tag_reply_content(agent_reply, post, client_id))
                
                # Trigger discussion with remaining agents
                async def on_discussion_reply(discussion_reply):
                    disc_reply = {
                        "id": store.get_next_reply_id(),
                        "post_id": post_id,
                        "author": discussion_reply["agent_name"],
                        "author_type": "agent",
                        "author_role": discussion_reply["agent_role"],
                        "content": discussion_reply["content"],
                        "created_at": datetime.now().isoformat(),
                        "likes": 0,
                        "visualization": discussion_reply.get("visualization"),
                        "is_discussion": True,
                        "tags": []
                    }
                    post["replies"].append(disc_reply)
                    await ws_manager.emit_new_reply(post_id, disc_reply, client_id)
                    # Auto-tag discussion reply
                    asyncio.create_task(auto_tag_reply_content(disc_reply, post, client_id))
                
                await trigger_agent_discussion(
                    previous_response=result.get("content", ""),
                    responding_agent=mentioned_role,
                    file_id=file_id,
                    original_context=user_message,
                    threshold=9,
                    post_id=post_id,
                    on_reply_generated=on_discussion_reply,
                    client_id=client_id,
                    image_file_path=image_file_path  # Pass image for discussion agents
                )
            return
        
        # ============ NO @MENTION: Discussion-based agent selection ============
        # All agents participate, highest scorer responds first (Round 1 mandatory)
        logger.info(f"Starting discussion-based analysis for POST {post_id}")
        
        # 실시간 reply 처리 콜백
        async def on_agent_reply(discussion_reply):
            viz = discussion_reply.get("visualization")
            if viz:
                logger.debug(f"Saving visualization for reply: keys={list(viz.keys()) if isinstance(viz, dict) else type(viz)}")
            agent_reply = {
                "id": store.get_next_reply_id(),
                "post_id": post_id,
                "author": discussion_reply["agent_name"],
                "author_type": "agent",
                "author_role": discussion_reply["agent_role"],
                "content": discussion_reply["content"],
                "created_at": datetime.now().isoformat(),
                "likes": 0,
                "visualization": viz,
                "tags": []
            }
            post["replies"].append(agent_reply)
            logger.info(f"Agent reply added to POST {post_id} by {discussion_reply['agent_name']} (has_viz={viz is not None})")
            await ws_manager.emit_new_reply(post_id, agent_reply, client_id)
            # Auto-tag the reply
            asyncio.create_task(auto_tag_reply_content(agent_reply, post, client_id))
        
        # Trigger discussion with ALL agents (responding_agent="" means no exclusion)
        await trigger_agent_discussion(
            previous_response=f"User question: {user_message}",
            responding_agent="",  # No agent to exclude - all participate
            file_id=file_id,
            original_context=user_message,
            threshold=9,
            post_id=post_id,
            on_reply_generated=on_agent_reply,
            client_id=client_id,
            image_file_path=image_file_path  # Pass image for discussion agents
        )
        
        logger.debug(f"Discussion-based analysis complete for POST {post_id}")
        
    except Exception as e:
        logger.error(f"Error in agent analysis for POST {post_id}: {str(e)}", exc_info=True)


async def add_agent_analysis_to_reply_background(post_id: int, post: dict, user_message: str, file_id: str, file_path: str, client_id: str = None, image_file_path: str = None):
    """
    Background task to add agent analysis as replies to a user's reply.
    Uses Discussion-based agent selection (no Triage agent).
    """
    from agents_sdk import (
        get_agent_by_role,
        get_agent_display_name,
        parse_mentions,
        run_agent_analysis,
        trigger_agent_discussion,
    )
    import random
    
    if not client_id:
        logger.error(f"client_id is required for reply analysis")
        return
    
    store = get_client_store(client_id)
    
    try:
        # ============ EXTRACT DATA SCOPE ============
        data_scope = post.get("data_scope")
        
        if data_scope:
            logger.debug(f"Using data_scope for reply analysis: {data_scope}")
        
        # ============ @MENTION PARSING ============
        mentioned_role, mentioned_agent_id, cleaned_message = parse_mentions(user_message, client_id)
        
        # Natural delay before agents respond
        delay = random.uniform(2.0, 4.0)
        logger.debug(f"Agents evaluating for {delay:.1f}s before replying to user reply in POST {post_id}...")
        await asyncio.sleep(delay)
        
        # ============ @MENTION: Direct routing to specific agent ============
        if mentioned_role:
            # Determine display name - use specific agent if mentioned by name
            if mentioned_agent_id:
                # User mentioned a specific agent by name.
                specific_agent = store.agent_registry.get(mentioned_agent_id)
                display_name = specific_agent["name"] if specific_agent else get_agent_display_name(mentioned_role, client_id)
                logger.info(f"@Mention detected in reply: specific agent '{display_name}' (id={mentioned_agent_id}) - routing directly")
            else:
                # User used a role keyword.
                display_name = get_agent_display_name(mentioned_role, client_id)
                logger.info(f"@Mention detected in reply: role {mentioned_role} -> '{display_name}' - routing directly")
            
            agent = get_agent_by_role(mentioned_role)
            
            if agent:
                # ============ BUILD REFERENCE CONTEXT FROM PREVIOUS REPLIES ============
                # Include POST content and all previous replies so agent knows the conversation
                reference_context = _build_post_context(post)
                
                result = await run_agent_analysis(
                    message=cleaned_message,
                    file_path=file_path,
                    file_id=file_id,
                    agent=agent,
                    post_id=post_id,
                    data_scope=data_scope,
                    client_id=client_id,
                    display_name=display_name,  # Pass custom name for typing indicator
                    agent_role=mentioned_role,  # Pass role for status row detection
                    reference_context=reference_context,  # Include previous conversation
                    image_file_path=image_file_path  # Pass image for Vision API
                )
                
                agent_reply = {
                    "id": store.get_next_reply_id(),
                    "post_id": post_id,
                    "author": display_name,
                    "author_type": "agent",
                    "author_role": mentioned_role,
                    "content": result.get("content", ""),
                    "created_at": datetime.now().isoformat(),
                    "likes": 0,
                    "visualization": result.get("visualization"),
                    "tags": []
                }
                
                post["replies"].append(agent_reply)
                await ws_manager.emit_new_reply(post_id, agent_reply, client_id)
                logger.info(f"@Mentioned agent {display_name} replied to user reply in POST {post_id}")
                # Auto-tag the mentioned agent reply
                asyncio.create_task(auto_tag_reply_content(agent_reply, post, client_id))
                
                # Trigger discussion with remaining agents
                async def on_mention_discussion_reply(discussion_reply):
                    disc_reply = {
                        "id": store.get_next_reply_id(),
                        "post_id": post_id,
                        "author": discussion_reply["agent_name"],
                        "author_type": "agent",
                        "author_role": discussion_reply["agent_role"],
                        "content": discussion_reply["content"],
                        "created_at": datetime.now().isoformat(),
                        "likes": 0,
                        "visualization": discussion_reply.get("visualization"),
                        "is_discussion": True,
                        "tags": []
                    }
                    post["replies"].append(disc_reply)
                    await ws_manager.emit_new_reply(post_id, disc_reply, client_id)
                    # Auto-tag discussion reply
                    asyncio.create_task(auto_tag_reply_content(disc_reply, post, client_id))
                
                await trigger_agent_discussion(
                    previous_response=result.get("content", ""),
                    responding_agent=mentioned_role,
                    file_id=file_id,
                    original_context=user_message,
                    threshold=9,
                    post_id=post_id,
                    on_reply_generated=on_mention_discussion_reply,
                    client_id=client_id,
                    image_file_path=image_file_path  # Pass image for discussion agents
                )
            return
        
        # ============ NO @MENTION: Discussion-based agent selection ============
        logger.info(f"Starting discussion-based analysis for user reply in POST {post_id}")
        
        # Build context from current post so agents know the full conversation
        post_context = _build_post_context(post)
        
        async def on_agent_reply(discussion_reply):
            agent_reply = {
                "id": store.get_next_reply_id(),
                "post_id": post_id,
                "author": discussion_reply["agent_name"],
                "author_type": "agent",
                "author_role": discussion_reply["agent_role"],
                "content": discussion_reply["content"],
                "created_at": datetime.now().isoformat(),
                "likes": 0,
                "visualization": discussion_reply.get("visualization"),
                "tags": []
            }
            post["replies"].append(agent_reply)
            logger.info(f"Agent reply added to user reply in POST {post_id} by {discussion_reply['agent_name']}")
            await ws_manager.emit_new_reply(post_id, agent_reply, client_id)
            # Auto-tag the reply
            asyncio.create_task(auto_tag_reply_content(agent_reply, post, client_id))
        
        # Trigger discussion with ALL agents
        # Put latest user reply FIRST so it survives truncation
        await trigger_agent_discussion(
            previous_response=f"[Latest User Message]\n{user_message}\n\n[Thread History]\n{post_context}",
            responding_agent="",  # No agent to exclude - all participate
            file_id=file_id,
            original_context=user_message,
            threshold=9,
            post_id=post_id,
            on_reply_generated=on_agent_reply,
            client_id=client_id,
            image_file_path=image_file_path  # Pass image for discussion agents
        )
        
        logger.debug(f"Discussion-based analysis complete for user reply in POST {post_id}")
        
    except Exception as e:
        logger.error(f"Error in agent analysis for user reply in POST {post_id}: {str(e)}", exc_info=True)


async def check_and_add_counterpoint_background(post_id: int, agent_role: str, original_content: str, file_id: str, client_id: str = None):
    """
    Background task to check and add counterpoint reply.
    This runs async to avoid blocking the main response.
    """
    if not client_id:
        logger.error(f"client_id is required for counterpoint")
        return
    
    store = get_client_store(client_id)
    
    try:
        from agents_sdk import check_and_trigger_counterpoint
        
        counterpoint = await check_and_trigger_counterpoint(
            agent_role,
            original_content,
            file_id,
            client_id  # Pass client_id for multi-agent support
        )
        
        if counterpoint:
            # Get the post
            post = store.get_post(post_id)
            if not post:
                logger.warning(f"POST {post_id} not found for counterpoint (client: {client_id[:20]}...)")
                return
            
            # Add counterpoint as a reply
            counterpoint_reply = {
                "id": store.get_next_reply_id(),
                "post_id": post_id,
                "author": counterpoint["agent_name"],
                "author_type": "agent",
                "author_role": counterpoint["agent_role"],
                "content": counterpoint["content"],
                "created_at": datetime.now().isoformat(),
                "likes": 0,
                "visualization": None,
                "is_counterpoint": True,
                "tags": []
            }
            post["replies"].append(counterpoint_reply)
            logger.info(f"Counterpoint reply added from {counterpoint['agent_name']}")
            
            # Emit via WebSocket
            await ws_manager.emit_new_reply(post_id, counterpoint_reply, client_id)
            # Auto-tag counterpoint reply
            asyncio.create_task(auto_tag_reply_content(counterpoint_reply, post, client_id))
            
    except Exception as cp_err:
        logger.error(f"Counterpoint background check failed: {cp_err}")


async def add_auto_collaboration_replies_background(proactive_post_id: int, proactive_content: str, file_id: str, file_path: str, client_id: str = None):
    """
    Background task to add auto-collaboration replies to a proactive post
    This adds a natural delay so replies appear after the post
    """
    from services.orchestration_service import trigger_agent_collaboration
    import random
    import asyncio
    
    if not client_id:
        logger.error(f"client_id is required for auto-collaboration")
        return
    
    store = get_client_store(client_id)
    
    try:
        # Natural delay before agents start replying (2-5 seconds)
        delay = random.uniform(2.0, 5.0)
        logger.debug(f"Waiting {delay:.1f}s before agents reply to proactive POST {proactive_post_id}...")
        await asyncio.sleep(delay)
        
        # Get the proactive post
        proactive_post = store.get_post(proactive_post_id)
        if not proactive_post:
            logger.error(f"Proactive POST {proactive_post_id} not found for auto-collaboration (client: {client_id[:20]}...)")
            return
        
        logger.info(f"Triggering auto-collaboration on proactive POST {proactive_post_id}")
        agent_replies = await trigger_agent_collaboration(
            proactive_post_content=proactive_content,
            proactive_post_id=proactive_post_id,
            file_id=file_id,
            file_path=file_path
        )
        
        # Add agent replies to the proactive post ONE BY ONE with delays
        for idx, agent_reply_data in enumerate(agent_replies):
            # Add delay between replies for natural feel
            if idx > 0:
                reply_delay = random.uniform(1.0, 3.0)
                logger.debug(f"Waiting {reply_delay:.1f}s before next agent reply...")
                await asyncio.sleep(reply_delay)
            
            viz_data = agent_reply_data.get('visualization')
            
            agent_reply = {
                "id": store.get_next_reply_id(),
                "post_id": proactive_post_id,
                "author": agent_reply_data['author'],
                "author_type": agent_reply_data['author_type'],
                "author_role": agent_reply_data.get('author_role'),
                "content": agent_reply_data['content'],
                "created_at": datetime.now().isoformat(),
                "likes": 0,
                "visualization": viz_data,
                "tags": []
            }
            
            proactive_post["replies"].append(agent_reply)
            logger.info(f"Added agent reply {idx+1}/{len(agent_replies)} to proactive POST {proactive_post_id}")
            
            # ========== WEBSOCKET: Emit new reply ==========
            await ws_manager.emit_new_reply(proactive_post_id, agent_reply, client_id)
            # Auto-tag the reply
            asyncio.create_task(auto_tag_reply_content(agent_reply, proactive_post, client_id))
        
        # ========== AGENT DISCUSSION TRIGGER FOR PROACTIVE POST ==========
        # After proactive post content, check if other agents want to add to the discussion
        # Skip if visualizer already replied (to avoid duplicate similar responses)
        try:
            from agents_sdk import trigger_agent_discussion
            
            # Check if visualizer already replied in this auto-collaboration
            has_viz_reply = any(r.get("author_role") == "visualization" for r in proactive_post.get("replies", []))
            
            # Skip discussion trigger if visualizer already added a reply
            # (prevents duplicate similar responses from the same agent type)
            if not has_viz_reply:
                # 실시간 reply 처리 콜백
                async def on_proactive_discussion_reply(discussion_reply):
                    disc_reply = {
                        "id": store.get_next_reply_id(),
                        "post_id": proactive_post_id,
                        "author": discussion_reply["agent_name"],
                        "author_type": "agent",
                        "author_role": discussion_reply["agent_role"],
                        "content": discussion_reply["content"],
                        "created_at": datetime.now().isoformat(),
                        "likes": 0,
                        "visualization": discussion_reply.get("visualization"),
                        "is_discussion": True,
                        "tags": []
                    }
                    proactive_post["replies"].append(disc_reply)
                    logger.info(f"Discussion reply added to proactive POST from {discussion_reply['agent_name']}")
                    await ws_manager.emit_new_reply(proactive_post_id, disc_reply, client_id)
                    # Auto-tag discussion reply
                    asyncio.create_task(auto_tag_reply_content(disc_reply, proactive_post, client_id))
                
                await trigger_agent_discussion(
                    previous_response=proactive_content,
                    responding_agent="scanner",  # Data Scout role to exclude
                    file_id=file_id,
                    original_context=proactive_content,
                    threshold=9,
                    post_id=proactive_post_id,
                    on_reply_generated=on_proactive_discussion_reply,  # 실시간 콜백
                    client_id=client_id  # NEW: pass client_id for isolation
                )
            else:
                logger.info(f"Skipping discussion trigger - visualizer already replied")
        except Exception as disc_err:
            logger.warning(f"Discussion trigger failed for proactive post: {disc_err}")
            
        logger.info(f"Completed auto-collaboration (with proactive viz) for proactive POST {proactive_post_id}")
        
    except Exception as collab_error:
        logger.error(f"Auto-collaboration failed for POST {proactive_post_id}: {str(collab_error)}")


async def create_proactive_post_background(post_id: int, file_id: str, file_path: str, client_id: str = None):
    """
    Background task to create proactive post
    This runs asynchronously so it doesn't block the reply response
    """
    from services.orchestration_service import proactive_trigger, summarize_discussion
    from agents_sdk import run_proactive_analysis
    from services import file_service
    
    if not client_id:
        logger.error(f"client_id is required for proactive post creation")
        return
    
    store = get_client_store(client_id)
    
    try:
        # Get the post
        post = store.get_post(post_id)
        if not post:
            logger.error(f"Post {post_id} not found for proactive post creation (client: {client_id[:20]}...)")
            return
        
        logger.info(f"Checking if Scout should intervene for POST {post_id}...")
        
        # ============ FIND ORIGINAL USER POST (for analysis goal) ============
        # Find the first user POST with file_metadata for this file
        original_user_post = None
        for p in store.posts_db:
            if (p.get("author_type") == "user" and 
                p.get("file_metadata", {}).get("file_id") == file_id):
                original_user_post = p
                break
        
        user_goal = original_user_post.get("content", "") if original_user_post else None
        if user_goal:
            logger.info(f"Found user's original goal: {user_goal[:50]}...")
        
        # ============ LLM-BASED DECISION ============
        # Ask LLM if Scout should intervene with a fresh perspective
        from services.orchestration_service import should_create_new_proactive_post
        
        logger.info(f"Asking LLM: Should Scout intervene for POST {post_id}?")
        llm_decision = await should_create_new_proactive_post(post, file_id, original_user_post)
        
        if not llm_decision.get("should_create", False):
            logger.info(f"LLM decided to WAIT: {llm_decision.get('reason', 'N/A')}")
            # Don't mark as triggered - allow future checks
            return
        
        # Extract direction from LLM decision
        exploration_direction = llm_decision.get("direction", "")
        
        logger.info(f"LLM approved Scout intervention")
        logger.info(f"Confidence: {llm_decision.get('confidence', 0):.2f}")
        logger.info(f"Reason: {llm_decision.get('reason', 'N/A')}")
        logger.info(f"Direction: {exploration_direction[:80] if exploration_direction else 'N/A'}...")
        proactive_trigger.mark_triggered(post_id, post.get("replies", []))
        
        # Natural delay before creating proactive post
        import random
        import asyncio
        
        # Get custom display name for Data Scout (client-aware)
        from agents_sdk import get_agent_display_name
        scanner_display_name = get_agent_display_name("scanner", client_id)
        
        # ========== WEBSOCKET: Emit typing for new post ==========
        await ws_manager.emit_agent_typing(scanner_display_name, None, "start", context="post", client_id=client_id, role="scanner")
        
        delay = random.uniform(3.0, 7.0)
        logger.debug(f"Waiting {delay:.1f}s before creating proactive post (natural delay)...")
        await asyncio.sleep(delay)
        
        # Get discussion summary
        discussion_summary = summarize_discussion(post)
        
        # Run proactive agent with direction and user goal
        proactive_result = await run_proactive_analysis(
            previous_discussion=discussion_summary,
            file_id=file_id,
            file_path=file_path,
            direction=exploration_direction,
            user_goal=user_goal,
            client_id=client_id
        )
        
        # Get file info
        file_info = file_service.get_file(file_id)
        
        # Create new proactive POST (WITHOUT replies initially)
        proactive_post_id = store.get_next_post_id()
        # Capture the last reply ID at trigger time as the branch origin
        origin_replies = post.get("replies", [])
        origin_reply_id = origin_replies[-1].get("id") if origin_replies else None

        new_proactive_post = {
            "id": proactive_post_id,
            "author": scanner_display_name,  # Use custom display name
            "author_type": "agent",
            "author_role": "scanner",
            "content": proactive_result.get('content', ''),
            "created_at": datetime.now().isoformat(),
            "likes": 0,
            "replies": [],  # Empty initially!
            "visualization": proactive_result.get('visualization'),
            "hitl_options": None,
            "file_metadata": file_info,
            "references_post_id": post_id,
            "references_reply_id": origin_reply_id,  # Track which reply triggered branching
            "client_id": client_id  # Track which client created this post
        }
        
        store.add_post(new_proactive_post)
        
        logger.info(f"Created proactive POST {proactive_post_id} (without replies)")
        
        # ========== WEBSOCKET: Emit new post and typing end ==========
        await ws_manager.emit_agent_typing(scanner_display_name, proactive_post_id, "end", context="post", client_id=client_id, role="scanner")
        await ws_manager.emit_new_post(new_proactive_post, client_id)
        
        # Schedule auto-collaboration as ANOTHER background task
        # This will add replies AFTER the post appears
        import asyncio
        asyncio.create_task(
            add_auto_collaboration_replies_background(
                proactive_post_id=proactive_post_id,
                proactive_content=new_proactive_post['content'],
                file_id=file_id,
                file_path=file_path,
                client_id=client_id  # NEW: pass client_id for isolation
            )
        )
        logger.info(f"Auto-collaboration scheduled for proactive POST {proactive_post_id}")
            
    except Exception as e:
        logger.error(f"Failed to create proactive post in background: {str(e)}")


@router.post("/reply")
async def create_reply(
    background_tasks: BackgroundTasks,
    client_id: str = Form(...),  # Required for client isolation
    post_id: int = Form(...),
    author: str = Form(...),
    author_type: str = Form(...),
    content: str = Form(...),
    author_role: Optional[str] = Form(None),
    visualization: Optional[str] = Form(None)
):
    """Create a reply to a post"""
    store = get_client_store(client_id)
    
    # Find the post
    post = store.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail=f"Post {post_id} not found for client {client_id[:20]}...")
    
    # Parse visualization
    import json
    viz_data = None
    if visualization:
        try:
            viz_data = json.loads(visualization)
        except:
            pass
    
    # Create reply
    new_reply = {
        "id": store.get_next_reply_id(),
        "post_id": post_id,
        "author": author,
        "author_type": author_type,
        "author_role": author_role,
        "content": content,
        "created_at": datetime.now().isoformat(),
        "likes": 0,
        "visualization": viz_data,
        "tags": []  # Auto-tagged + user-added tags
    }
    
    post["replies"].append(new_reply)
    
    # ============ GET FILE CONTEXT ============
    file_metadata = post.get("file_metadata", {})
    file_id = file_metadata.get("file_id") if file_metadata else None
    
    # FALLBACK: If no file_id found, look for context from other posts
    if not file_id:
        for p in reversed(store.posts_db):
            if p and p.get('file_metadata') and p['file_metadata'].get('file_id'):
                file_id = p['file_metadata']['file_id']
                logger.debug(f"Found context file_id from feed: {file_id}")

                # Update the post with file_metadata for future references
                from services import file_service
                context_file_info = file_service.get_file(file_id)
                if context_file_info:
                    post['file_metadata'] = context_file_info
                    file_metadata = context_file_info
                    logger.debug(f"Updated POST {post_id} with file_metadata from context")
                break
    
    logger.info(f"Reply created for POST {post_id} by {author} ({author_type})")
    logger.info(f"Post has {len(post['replies'])} total replies")
    logger.info(f"file_id: {file_id}")
    
    # ============ USER REPLY → AGENT ANALYSIS + DISCUSSION ============
    if author_type == "user":
        if file_id:
            from services import file_service
            file_info = file_service.get_file(file_id)
            file_path = file_info.get('file_path') if file_info else None
            
            if file_path:
                post_image_path = post.get("image_file_path")
                logger.info(f"User reply detected in POST {post_id}, triggering agent analysis")
                asyncio.create_task(
                    add_agent_analysis_to_reply_background(
                        post_id=post_id,
                        post=post,
                        user_message=content,
                        file_id=file_id,
                        file_path=file_path,
                        client_id=client_id,
                        image_file_path=post_image_path
                    )
                )
                asyncio.create_task(
                    create_proactive_post_background(
                        post_id=post_id,
                        file_id=file_id,
                        file_path=file_path,
                        client_id=client_id
                    )
                )
    
    # ============ AUTO-TAG REPLY ============
    asyncio.create_task(auto_tag_reply_content(new_reply, post, client_id))
    
    # ============ AUTO SUMMARY TRIGGER ============
    # Check if summary should be auto-generated after reply
    if file_id:
        check_and_trigger_summary_background(file_id, client_id)
    
    # Return reply immediately (agent analysis will happen in background)
    return {"reply": new_reply}


def check_and_trigger_summary_background(file_id: str, client_id: str):
    """
    Check if summary should be auto-generated and trigger if needed
    Runs in background, non-blocking
    """
    import asyncio
    
    if not client_id:
        logger.warning("client_id is required for summary trigger")
        return
    
    store = get_client_store(client_id)
    
    try:
        # Count posts and replies for this file (from this client's store)
        related_posts = [
            post for post in store.posts_db 
            if post and post.get('file_metadata', {}).get('file_id') == file_id
        ]
        
        post_count = len(related_posts)
        reply_count = sum(len(post.get('replies', [])) for post in related_posts if post)
        
        # Check if should trigger
        if summary_trigger.should_generate_summary(file_id, post_count, reply_count):
            logger.info(f"Auto-triggering summary generation for file {file_id} (client: {client_id[:20]}...)")
            
            # Schedule summary generation in background
            asyncio.create_task(generate_summary_background(file_id, post_count, reply_count, client_id))
        
    except Exception as e:
        logger.error(f"Summary trigger check failed: {str(e)}", exc_info=True)


async def generate_summary_background(file_id: str, post_count: int, reply_count: int, client_id: str = None):
    """
    Generate summary in background
    """
    from agents_sdk import generate_summary
    
    try:
        logger.info(f"Starting summary generation for file {file_id} (client: {client_id[:20] if client_id else 'N/A'}...)")
        
        # Generate summary
        result = await generate_summary(file_id, client_id)
        
        if result.get('success'):
            # Save to summary database
            from routers.analysis import summaries_db, get_next_summary_id, update_summary_id
            
            new_summary_id = get_next_summary_id()
            
            summary_data = {
                "id": new_summary_id,
                "file_id": file_id,
                "content": result['content'],
                "next_steps": result.get('next_steps', []),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "post_count": post_count,
                "reply_count": reply_count
            }
            
            summary_key = f"{client_id}_{file_id}"
            summaries_db[summary_key] = summary_data
            
            # Mark as generated
            summary_trigger.mark_summary_generated(
                file_id=file_id,
                post_count=post_count,
                reply_count=reply_count,
                summary_id=new_summary_id
            )
            
            logger.info(f"Summary auto-generated for file {file_id}: {new_summary_id}")
        else:
            logger.error(f"Summary generation failed: {result.get('error')}")
    
    except Exception as e:
        logger.error(f"Background summary generation failed: {str(e)}", exc_info=True)


# ============ MEDIA GALLERY ENDPOINT ============

@router.get("/media")
async def get_media(client_id: str = Query(..., description="Client ID for session isolation")):
    """
    Get all media (charts and images) for the Media Gallery.
    Returns:
        - charts: Agent-generated visualizations from posts/replies
        - images: User-uploaded images
    """
    store = get_client_store(client_id)
    
    charts = []
    images = []
    
    # Image extensions for detecting uploaded images
    image_extensions = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
    
    # Collect charts and images from all posts (from this client's store)
    for post in store.posts_db:
        post_id = post.get("id")
        post_created = post.get("created_at")
        
        # Check if post has a visualization (chart)
        if post.get("visualization"):
            viz = post["visualization"]
            # Note: visualization IS the Vega-Lite spec, use it directly
            charts.append({
                "id": f"post_{post_id}_viz",
                "type": "chart",
                "title": viz.get("title", "Untitled Chart"),
                "chart_type": viz.get("mark", viz.get("chart_type", "unknown")),
                "chart_data": viz,  # Pass entire spec - viz IS the Vega-Lite spec
                "source": f"Post #{post_id}",
                "source_post_id": post_id,
                "author": post.get("author", "Agent"),
                "created_at": post_created
            })
        
        # Check if post has an uploaded image
        file_meta = post.get("file_metadata")
        if file_meta:
            file_type = file_meta.get("file_type", "").lower()
            if file_type in image_extensions:
                images.append({
                    "id": f"post_{post_id}_img",
                    "type": "image",
                    "title": file_meta.get("original_filename", "Image"),
                    "image_url": file_meta.get("image_url") or f"/uploads/{file_meta.get('file_id')}.{file_type}",
                    "source": f"Post #{post_id}",
                    "source_post_id": post_id,
                    "author": post.get("author", "Unknown"),
                    "created_at": post_created,
                    "width": file_meta.get("width"),
                    "height": file_meta.get("height")
                })
        
        # Check replies for visualizations
        for reply in post.get("replies", []):
            reply_id = reply.get("id")
            reply_created = reply.get("created_at")
            
            if reply.get("visualization"):
                viz = reply["visualization"]
                charts.append({
                    "id": f"reply_{reply_id}_viz",
                    "type": "chart",
                    "title": viz.get("title", "Untitled Chart"),
                    "chart_type": viz.get("mark", viz.get("chart_type", "unknown")),
                    "chart_data": viz,  # Pass entire spec - viz IS the Vega-Lite spec
                    "source": f"Reply in Post #{post_id}",
                    "source_post_id": post_id,
                    "source_reply_id": reply_id,
                    "author": reply.get("author", "Agent"),
                    "created_at": reply_created
                })
    
    # Sort by created_at (newest first)
    charts.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    images.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    return {
        "charts": charts,
        "images": images,
        "total_charts": len(charts),
        "total_images": len(images)
    }


# ==================== Tag Management ====================

VALID_TAGS = {"hypothesis", "evidence", "question", "todo", "insight"}

@router.patch("/post/{post_id}/tags")
async def update_post_tags(
    post_id: int, 
    request: dict,
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """
    Update tags for a post (add or remove)
    
    Args:
        post_id: Post ID
        client_id: Client ID for session isolation
        request: {
            "action": "add" | "remove" | "set",
            "tags": ["evidence", "insight"]
        }
    """
    store = get_client_store(client_id)
    
    action = request.get("action", "set")
    tags = request.get("tags", [])
    
    # Validate tags
    valid_tags = [t for t in tags if t in VALID_TAGS]
    
    # Find post
    post = store.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail=f"Post {post_id} not found")
    
    # Ensure tags field exists
    if "tags" not in post:
        post["tags"] = []
    
    if action == "add":
        for tag in valid_tags:
            if tag not in post["tags"]:
                post["tags"].append(tag)
    elif action == "remove":
        post["tags"] = [t for t in post["tags"] if t not in valid_tags]
    else:  # set
        post["tags"] = valid_tags
    
    logger.info(f"Updated post {post_id} tags: {post['tags']}")
    
    return {"id": post_id, "tags": post["tags"]}


@router.patch("/post/{post_id}/reply/{reply_id}/tags")
async def update_reply_tags(
    post_id: int, 
    reply_id: int, 
    request: dict,
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """
    Update tags for a reply
    
    Args:
        post_id: Parent post ID
        reply_id: Reply ID
        client_id: Client ID for session isolation
        request: {
            "action": "add" | "remove" | "set",
            "tags": ["evidence", "insight"]
        }
    """
    store = get_client_store(client_id)
    
    action = request.get("action", "set")
    tags = request.get("tags", [])
    
    # Validate tags
    valid_tags = [t for t in tags if t in VALID_TAGS]
    
    # Find post
    post = store.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail=f"Post {post_id} not found")
    
    # Find reply
    reply = next((r for r in post.get("replies", []) if r.get("id") == reply_id), None)
    if not reply:
        raise HTTPException(status_code=404, detail="Reply not found")
    
    # Ensure tags field exists
    if "tags" not in reply:
        reply["tags"] = []
    
    if action == "add":
        for tag in valid_tags:
            if tag not in reply["tags"]:
                reply["tags"].append(tag)
    elif action == "remove":
        reply["tags"] = [t for t in reply["tags"] if t not in valid_tags]
    else:  # set
        reply["tags"] = valid_tags
    
    logger.info(f"Updated reply {reply_id} tags: {reply['tags']}")
    
    return {"post_id": post_id, "reply_id": reply_id, "tags": reply["tags"]}


# ==================== Save/Bookmark ====================

@router.patch("/post/{post_id}/save")
async def toggle_save_post(
    post_id: int,
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """Toggle save status for a post"""
    store = get_client_store(client_id)
    post = store.get_post(post_id)
    
    if not post:
        raise HTTPException(status_code=404, detail=f"Post {post_id} not found")
    
    # Toggle is_saved
    post["is_saved"] = not post.get("is_saved", False)
    
    logger.info(f"Post {post_id} saved: {post['is_saved']}")
    
    # Send WebSocket update
    await ws_manager.send_to_client(client_id, {
        "type": "post_saved",
        "post_id": post_id,
        "is_saved": post["is_saved"]
    })
    
    return {"post_id": post_id, "is_saved": post["is_saved"]}


# ==================== Search ====================

@router.get("/search")
async def search_posts(
    q: str = Query(..., min_length=1, description="Search query"),
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """
    Search posts and replies by keyword.
    
    Args:
        q: Search query string
        client_id: Client ID for session isolation
        
    Returns:
        List of matching posts and replies with highlights
    """
    store = get_client_store(client_id)
    query = q.lower().strip()
    
    results = []
    
    for post in store.posts_db:
        if not post:
            continue
            
        post_id = post.get("id")
        post_content = post.get("content", "").lower()
        
        # Check if post matches
        if query in post_content:
            # Find snippet around the match
            idx = post_content.find(query)
            start = max(0, idx - 50)
            end = min(len(post_content), idx + len(query) + 50)
            snippet = post.get("content", "")[start:end]
            if start > 0:
                snippet = "..." + snippet
            if end < len(post_content):
                snippet = snippet + "..."
            
            results.append({
                "type": "post",
                "post_id": post_id,
                "reply_id": None,
                "author": post.get("author", "Unknown"),
                "author_type": post.get("author_type", "user"),
                "author_role": post.get("author_role"),
                "snippet": snippet,
                "created_at": post.get("created_at")
            })
        
        # Search replies
        for reply in post.get("replies", []):
            reply_content = reply.get("content", "").lower()
            
            if query in reply_content:
                idx = reply_content.find(query)
                start = max(0, idx - 50)
                end = min(len(reply_content), idx + len(query) + 50)
                snippet = reply.get("content", "")[start:end]
                if start > 0:
                    snippet = "..." + snippet
                if end < len(reply_content):
                    snippet = snippet + "..."
                
                results.append({
                    "type": "reply",
                    "post_id": post_id,
                    "reply_id": reply.get("id"),
                    "author": reply.get("author", "Unknown"),
                    "author_type": reply.get("author_type", "user"),
                    "author_role": reply.get("author_role"),
                    "snippet": snippet,
                    "created_at": reply.get("created_at")
                })
    
    # Sort by created_at descending (newest first)
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    logger.info(f"Search '{q}' returned {len(results)} results for client {client_id[:20]}...")
    
    return {
        "query": q,
        "count": len(results),
        "results": results[:50]  # Limit to 50 results
    }


# ==================== Insight Timeline ====================

@router.get("/timeline")
async def get_insight_timeline(
    client_id: str = Query(..., description="Client ID for session isolation"),
    tag_filter: Optional[str] = Query(None, description="Filter by tag (comma-separated)")
):
    """
    Get insight timeline with tagged items and their connections.
    
    Args:
        client_id: Client ID for session isolation
        tag_filter: Optional comma-separated list of tags to filter by
        
    Returns:
        Timeline items sorted by time with connections
    """
    store = get_client_store(client_id)
    
    # Parse tag filter
    filter_tags = set()
    if tag_filter:
        filter_tags = set(t.strip().lower() for t in tag_filter.split(","))
    
    timeline_items = []
    
    for post in store.posts_db:
        if not post:
            continue
        
        post_tags = post.get("tags", [])
        
        # Always add post to timeline (for structural view)
        # Filter by tags only if filter is specified
        if not filter_tags or any(t in filter_tags for t in post_tags):
            timeline_items.append({
                "id": f"post_{post.get('id')}",
                "type": "post",
                "post_id": post.get("id"),
                "reply_id": None,
                "author": post.get("author", "Unknown"),
                "author_type": post.get("author_type", "user"),
                "author_role": post.get("author_role"),
                "content": post.get("content", ""),
                "tags": post_tags,
                "connections": post.get("connections", []),
                "created_at": post.get("created_at"),
                "has_visualization": post.get("visualization") is not None,
                "reply_count": len(post.get("replies", []))
            })
        
        # Add all replies (for structural view)
        for reply in post.get("replies", []):
            reply_tags = reply.get("tags", [])
            
            # Filter by tags only if filter is specified
            if not filter_tags or any(t in filter_tags for t in reply_tags):
                timeline_items.append({
                    "id": f"reply_{post.get('id')}_{reply.get('id')}",
                    "type": "reply",
                    "post_id": post.get("id"),
                    "reply_id": reply.get("id"),
                    "author": reply.get("author", "Unknown"),
                    "author_type": reply.get("author_type", "user"),
                    "author_role": reply.get("author_role"),
                    "content": reply.get("content", ""),
                    "tags": reply_tags,
                    "connections": reply.get("connections", []),
                    "created_at": reply.get("created_at"),
                    "has_visualization": reply.get("visualization") is not None
                })
    
    # Sort by created_at (oldest first for timeline)
    timeline_items.sort(key=lambda x: x.get("created_at", ""))
    
    # Calculate summary stats
    tag_counts = {}
    for item in timeline_items:
        for tag in item.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    
    total_connections = sum(len(item.get("connections", [])) for item in timeline_items)
    
    logger.debug(f"Timeline for client {client_id[:20]}...: {len(timeline_items)} items, {total_connections} connections")
    
    return {
        "items": timeline_items,
        "count": len(timeline_items),
        "tag_counts": tag_counts,
        "total_connections": total_connections
    }


# ==================== Like Post ====================

@router.patch("/post/{post_id}/like")
async def toggle_like_post(
    post_id: int,
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """Toggle like status for a post and generate recommendations if liked"""
    store = get_client_store(client_id)
    post = store.get_post(post_id)
    
    if not post:
        raise HTTPException(status_code=404, detail=f"Post {post_id} not found")
    
    # Toggle is_liked
    was_liked = post.get("is_liked", False)
    post["is_liked"] = not was_liked
    
    logger.info(f"Post {post_id} liked: {post['is_liked']}")
    
    # If newly liked, generate recommendations
    recommendations = []
    if post["is_liked"] and not was_liked:
        recommendations = await generate_like_recommendations(post, store, client_id)
        post["like_recommendations"] = recommendations
    elif not post["is_liked"]:
        post["like_recommendations"] = []
    
    # Send WebSocket update
    await ws_manager.send_to_client(client_id, {
        "type": "post_liked",
        "post_id": post_id,
        "is_liked": post["is_liked"],
        "recommendations": post.get("like_recommendations", [])
    })
    
    return {
        "post_id": post_id,
        "is_liked": post["is_liked"],
        "recommendations": post.get("like_recommendations", [])
    }


async def generate_like_recommendations(post: dict, store, client_id: str) -> list:
    """Generate follow-up recommendations based on a liked post"""
    import traceback

    try:
        # Get dataset context
        file_metadata = None
        file_id = None
        all_posts = store.get_all_posts() if hasattr(store, 'get_all_posts') else []
        
        for p in all_posts:
            if p and isinstance(p, dict) and p.get("file_metadata"):
                file_metadata = p["file_metadata"]
                if isinstance(file_metadata, dict):
                    file_id = file_metadata.get("file_id")
                break
        
        dataset_context = ""
        if file_metadata and isinstance(file_metadata, dict):
            columns = file_metadata.get("columns", [])
            row_count = file_metadata.get("row_count", 0)
            # Ensure columns is a list
            if isinstance(columns, list):
                dataset_context = f"\n\nDataset: {row_count} rows, columns: {', '.join(str(c) for c in columns[:15])}"
            elif isinstance(columns, int):
                dataset_context = f"\n\nDataset: {row_count} rows, {columns} columns"
        
        # Get conversation context from replies
        conversation_context = ""
        if post.get("replies"):
            conversation_context = "\n\nDiscussion so far:\n"
            for reply in post.get("replies", [])[:5]:
                author = reply.get("author", "Unknown")
                content = reply.get("content", "")[:200]
                conversation_context += f"- {author}: {content}...\n"
        
        # Get language preference
        from agents_sdk import resolve_language
        rec_lang = resolve_language(client_id=client_id, message=post.get('content', ''))
        
        prompt = build_like_recommendations_prompt(
            language=rec_lang,
            post_content=post.get('content', ''),
            dataset_context=dataset_context,
            conversation_context=conversation_context,
        )

        # Use simple OpenAI call instead of run_agent_analysis to avoid complexity
        from openai import AsyncOpenAI
        
        client = AsyncOpenAI()
        response = await client.chat.completions.create(
            model=settings.UTILITY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_completion_tokens=200
        )
        
        response_text = response.choices[0].message.content or ""
        
        # Parse JSON response
        import json
        import re
        
        json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
        if json_match:
            recommendations = json.loads(json_match.group())
            if isinstance(recommendations, list) and len(recommendations) > 0:
                from agents_sdk import _strip_backticks
                recommendations = [_strip_backticks(r) if isinstance(r, str) else r for r in recommendations]
                logger.info(f"Generated {len(recommendations)} recommendations for liked post {post.get('id')}")
                return recommendations[:3]
        
        return []
        
    except Exception as e:
        logger.error(f"Failed to generate like recommendations: {e}\n{traceback.format_exc()}")
        return []


# ==================== Tree View API ====================

@router.get("/tree")
async def get_tree_data(
    client_id: str = Query(..., description="Client ID for session isolation")
):
    """
    Get tree structure for visualization.
    - Root: Dataset with LLM summary
    - Level 2: Posts with LLM summary of discussion
    - Level 3+: Replies with preview, branching based on semantic connections
    """
    store = get_client_store(client_id)
    
    from openai import AsyncOpenAI
    import json
    
    posts = store.posts_db
    
    if not posts:
        return {"root": None, "nodes": []}
    
    # Find dataset info from first post with file_metadata
    dataset_info = None
    file_id = None
    for post in posts:
        if post and post.get("file_metadata"):
            dataset_info = post["file_metadata"]
            file_id = dataset_info.get("file_id")
            break
    
    if not dataset_info:
        return {"root": None, "nodes": []}
    
    # Initialize OpenAI client
    openai_client = AsyncOpenAI()
    
    # Generate dataset summary
    dataset_summary = await generate_dataset_summary(openai_client, dataset_info, client_id=client_id)
    
    root = {
        "id": f"dataset_{file_id}",
        "name": dataset_info.get("original_filename", "Dataset"),
        "summary": dataset_summary,
        "type": "dataset",
        "metadata": {
            "rows": dataset_info.get("rows"),
            "columns": dataset_info.get("columns"),
            "file_type": dataset_info.get("file_type")
        }
    }
    
    nodes = []
    
    # Process each post
    for post in posts:
        if not post:
            continue
        
        post_id = post.get("id")
        author = post.get("author", "Unknown")
        content = post.get("content", "")
        replies = post.get("replies", [])
        
        # Skip user system posts
        if post.get("author_type") == "system":
            continue
        
        # Generate post discussion summary and short title
        post_summary, post_title = await asyncio.gather(
            generate_post_summary(openai_client, post, replies, client_id=client_id),
            generate_post_title(openai_client, post, replies, client_id=client_id)
        )
        
        # Determine parent: use references_post_id/references_reply_id if derived
        ref_post_id = post.get("references_post_id")
        if ref_post_id is not None:
            ref_post_obj = store.get_post(ref_post_id)
            if ref_post_obj and ref_post_obj.get("author_type") != "system":
                # Use the specific origin reply that triggered the branch
                ref_reply_id = post.get("references_reply_id")
                if ref_reply_id is not None:
                    post_parent_id = f"reply_{ref_post_id}_{ref_reply_id}"
                else:
                    post_parent_id = f"post_{ref_post_id}"
            else:
                post_parent_id = f"dataset_{file_id}"
        else:
            post_parent_id = f"dataset_{file_id}"

        # Add post node
        nodes.append({
            "id": f"post_{post_id}",
            "parentId": post_parent_id,
            "name": author,
            "summary": post_summary,
            "title": post_title,
            "type": "post",
            "postId": post_id,
            "authorRole": post.get("author_role"),
            "authorType": post.get("author_type"),
            "hasVisualization": post.get("visualization") is not None,
            "created_at": post.get("created_at")
        })
        
        # Process replies with branching logic
        prev_reply_id = None
        for i, reply in enumerate(replies):
            reply_id = reply.get("id")
            reply_author = reply.get("author", "Unknown")
            reply_content = reply.get("content", "")
            connections = reply.get("connections", [])
            
            # Determine if this reply branches
            # Branch if: contradicts or questions previous items
            is_branch = False
            connection_type = None
            for conn in connections:
                relation = conn.get("relation", "")
                if relation in ["contradicts", "questions"]:
                    is_branch = True
                    connection_type = relation
                    break
            
            # Determine parent: always linear chain (first reply -> post, rest -> previous reply)
            # Semantic connections (contradicts/questions) are shown via arcs, not lane splits
            if i == 0:
                parent_id = f"post_{post_id}"
            else:
                parent_id = f"reply_{post_id}_{prev_reply_id}"
            
            # Truncate preview
            preview = reply_content[:100] + "..." if len(reply_content) > 100 else reply_content
            
            nodes.append({
                "id": f"reply_{post_id}_{reply_id}",
                "parentId": parent_id,
                "name": reply_author,
                "preview": preview,
                "type": "reply",
                "postId": post_id,
                "replyId": reply_id,
                "authorRole": reply.get("author_role"),
                "authorType": reply.get("author_type"),
                "branch": is_branch,
                "connectionType": connection_type,
                "hasVisualization": reply.get("visualization") is not None,
                "created_at": reply.get("created_at")
            })
            
            prev_reply_id = reply_id
    
    logger.info(f"Tree data generated: 1 root + {len(nodes)} nodes for client {client_id[:20]}...")
    
    return {"root": root, "nodes": nodes}


def _get_language_instruction(client_id: str, message: str = "") -> str:
    """Return a concise language instruction based on the client's current setting."""
    from agents_sdk import resolve_language

    lang = resolve_language(client_id=client_id, message=message)
    return "Respond in Korean." if lang == "Korean" else "Respond in English."


async def generate_dataset_summary(client, dataset_info: dict, client_id: str) -> str:
    """Generate a concise summary of the dataset using GPT 5.1"""
    try:
        filename = dataset_info.get("original_filename", "Unknown")
        rows = dataset_info.get("rows", "?")
        columns = dataset_info.get("columns", "?")
        column_names = dataset_info.get("column_names", [])
        language_instruction = _get_language_instruction(client_id)
        
        prompt = f"""Summarize this dataset in 1-2 sentences (max 50 words):
- Filename: {filename}
- Rows: {rows}, Columns: {columns}
- Column names: {', '.join(column_names[:10]) if column_names else 'Unknown'}

Focus on what kind of data this is and its purpose.
{language_instruction}"""

        response = await client.chat.completions.create(
            model=settings.UTILITY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_completion_tokens=100
        )
        
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"Dataset summary generation failed: {e}")
        return f"{dataset_info.get('original_filename', 'Dataset')} - {dataset_info.get('rows', '?')} rows"


async def generate_post_title(client, post: dict, replies: list, client_id: str) -> str:
    """Generate a short 2-5 word title for the post thread."""
    try:
        post_content = truncate_text(post.get("content", ""), 90, preserve="head")
        reply_snippets = " | ".join(
            truncate_text(r.get("content", ""), 35, preserve="head") for r in replies[:3]
        )
        language_instruction = _get_language_instruction(client_id, message=post_content)

        response = await client.chat.completions.create(
            model=settings.UTILITY_MODEL,
            messages=[{"role": "user", "content": f"Give a 2-5 word title (no quotes) that captures the core analysis topic.\n{language_instruction}\n\nQuestion: {post_content}\nDiscussion: {reply_snippets}"}],
            temperature=0.2,
            max_completion_tokens=20
        )

        title = (response.choices[0].message.content or "").strip().strip('"\'')
        return title[:40]
    except Exception as e:
        logger.error(f"Post title generation failed: {e}")
        return post.get("content", "")[:30]


async def generate_post_summary(client, post: dict, replies: list, client_id: str) -> str:
    """Generate a summary of the post discussion (post + replies) using GPT 5.1"""
    try:
        post_content = truncate_text(post.get("content", ""), 140, preserve="head")
        post_author = post.get("author", "Unknown")
        language_instruction = _get_language_instruction(client_id, message=post_content)
        
        # Build discussion context
        discussion = f"[{post_author}]: {post_content}\n"
        for reply in replies[:5]:  # Limit to first 5 replies
            reply_author = reply.get("author", "Unknown")
            reply_content = truncate_text(reply.get("content", ""), 65, preserve="head")
            discussion += f"[{reply_author}]: {reply_content}\n"
        
        prompt = f"""Summarize this data analysis discussion in 1-2 sentences (max 40 words):

{discussion}

Focus on key findings, insights, or questions raised.
{language_instruction}"""

        response = await client.chat.completions.create(
            model=settings.UTILITY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_completion_tokens=80
        )
        
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"Post summary generation failed: {e}")
        return post.get("content", "")[:100] + "..."
