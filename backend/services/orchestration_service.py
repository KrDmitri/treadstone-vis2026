"""
Orchestration service for managing proactive agent triggers and collaboration
"""
from typing import Optional, Dict, Any
from datetime import datetime
from utils.logger import logger
from config import settings
from prompts import (
    AUTO_VISUALIZATION_SYSTEM_PROMPT,
    SCOUT_TRIGGER_SYSTEM_PROMPT,
    build_auto_visualization_prompt,
    build_scout_trigger_prompt,
)
import json


class ProactiveAgentTrigger:
    """
    Manages proactive agent triggers based on human interaction
    
    Flow:
    1. Basic rule: POST has enough human replies since last trigger
    2. LLM decision: Ask if discussion is ready for new proactive post
    3. If YES: Create new post and mark reply count
    4. If NO: Don't mark (can check again later)
    
    Re-trigger: Every 5 additional human replies after first trigger
    """
    
    # Thresholds
    FIRST_TRIGGER_THRESHOLD = 2   # First trigger at 2 human replies
    RE_TRIGGER_INTERVAL = 5       # Re-trigger every 5 additional human replies
    
    def __init__(self):
        # Track human reply count at last trigger for each post
        self.triggered_at_count: Dict[int, int] = {}  # post_id -> human_reply_count
        self.last_trigger_time: Optional[datetime] = None
    
    def count_human_replies(self, post_replies: list) -> int:
        """Count human replies in a post"""
        return len([r for r in post_replies if r.get('author_type') == 'user'])
    
    def should_trigger(self, post_id: int, post_replies: list) -> bool:
        """
        Determine if proactive agent should be triggered
        
        Conditions:
        - First trigger: 2+ human replies
        - Re-trigger: 5+ additional human replies since last trigger
        
        Args:
            post_id: ID of the post to check
            post_replies: List of replies to the post
            
        Returns:
            True if should trigger, False otherwise
        """
        human_count = self.count_human_replies(post_replies)
        
        if post_id not in self.triggered_at_count:
            # Never triggered for this post
            if human_count >= self.FIRST_TRIGGER_THRESHOLD:
                logger.info(f"First trigger condition met for Post {post_id}: {human_count} human replies")
                return True
            else:
                logger.debug(f"Post {post_id} has {human_count} human replies (need {self.FIRST_TRIGGER_THRESHOLD})")
                return False
        else:
            # Already triggered before - check for re-trigger
            last_count = self.triggered_at_count[post_id]
            replies_since = human_count - last_count
            
            if replies_since >= self.RE_TRIGGER_INTERVAL:
                logger.info(f"Re-trigger condition met for Post {post_id}: {replies_since} new human replies since last trigger (total: {human_count})")
                return True
            else:
                logger.debug(f"Post {post_id}: {replies_since}/{self.RE_TRIGGER_INTERVAL} new replies for re-trigger")
                return False
    
    def mark_triggered(self, post_id: int, post_replies: list = None):
        """Mark post as triggered with current human reply count"""
        if post_replies:
            human_count = self.count_human_replies(post_replies)
        else:
            # Fallback: use previous count + interval (shouldn't happen normally)
            human_count = self.triggered_at_count.get(post_id, 0) + self.RE_TRIGGER_INTERVAL
        
        self.triggered_at_count[post_id] = human_count
        self.last_trigger_time = datetime.now()
        logger.info(f"Marked Post {post_id} as triggered at {human_count} human replies")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get trigger statistics for debugging"""
        return {
            "triggered_posts_count": len(self.triggered_at_count),
            "triggered_post_ids": list(self.triggered_at_count.keys()),
            "triggered_at_count": dict(self.triggered_at_count),
            "last_trigger_time": self.last_trigger_time.isoformat() if self.last_trigger_time else None
        }


class SummaryTrigger:
    """
    Manages automatic summary generation based on activity
    
    Trigger conditions:
    - 5 new POSTs since last summary
    - OR 10 new REPLYs since last summary
    """
    
    def __init__(self):
        self.summaries_by_file: Dict[str, dict] = {}  # file_id -> summary metadata
    
    def should_generate_summary(
        self, 
        file_id: str,
        current_post_count: int,
        current_reply_count: int
    ) -> bool:
        """
        Determine if summary should be auto-generated
        
        Conditions:
        - 5+ new POSTs OR 10+ new REPLYs since last summary
        
        Args:
            file_id: File ID to check
            current_post_count: Current total number of posts
            current_reply_count: Current total number of replies
            
        Returns:
            True if should generate summary, False otherwise
        """
        # Get last summary info
        last_summary = self.summaries_by_file.get(file_id, {})
        last_post_count = last_summary.get('post_count', 0)
        last_reply_count = last_summary.get('reply_count', 0)
        
        # Calculate new content
        new_posts = current_post_count - last_post_count
        new_replies = current_reply_count - last_reply_count
        
        # Check trigger conditions
        MIN_POSTS = 5
        MIN_REPLIES = 10
        
        should_trigger = (new_posts >= MIN_POSTS) or (new_replies >= MIN_REPLIES)
        
        if should_trigger:
            logger.info(f"Summary trigger for file {file_id}: {new_posts} new posts, {new_replies} new replies")
        
        return should_trigger
    
    def mark_summary_generated(
        self, 
        file_id: str, 
        post_count: int, 
        reply_count: int,
        summary_id: int
    ):
        """Record that summary was generated"""
        self.summaries_by_file[file_id] = {
            'summary_id': summary_id,
            'post_count': post_count,
            'reply_count': reply_count,
            'generated_at': datetime.now().isoformat()
        }
        logger.info(f"Marked summary generated for file {file_id} at POST:{post_count}, REPLY:{reply_count}")
    
    def get_last_summary(self, file_id: str) -> Optional[dict]:
        """Get last summary metadata for a file"""
        return self.summaries_by_file.get(file_id)


async def should_create_new_proactive_post(post_data: dict, file_id: str, original_user_post: dict = None) -> Dict[str, Any]:
    """
    Use LLM to decide if Scout should intervene with a new perspective.
    
    This is NOT about "conversation ending" - it's about:
    - Detecting when a fresh perspective would help the user
    - Suggesting a specific direction aligned with user's analysis goal
    - Proactively offering insights during active analysis
    
    Args:
        post_data: Current post with content and replies
        file_id: Dataset file ID for context
        original_user_post: The user's first POST (analysis goal/direction)
        
    Returns:
        {
            "should_create": bool,
            "reason": str,
            "confidence": float,
            "direction": str  # What Scout should explore
        }
    """
    from openai import AsyncOpenAI
    from config import settings
    
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    
    # Extract user's original analysis direction
    user_direction = ""
    if original_user_post:
        user_direction = f"""
**User's Original Analysis Goal:**
{original_user_post.get('content', '')[:400]}
"""
    
    # Build current conversation context
    discussion_context = f"""
**Current Discussion Post by {post_data['author']}:**
{post_data['content'][:300]}

**Recent Conversation ({len(post_data['replies'])} replies):**
"""
    
    # Include last 6 replies with more context
    for reply in post_data['replies'][-6:]:
        author = reply.get('author', 'Unknown')
        author_type = reply.get('author_type', '')
        content = reply.get('content', '')[:200]
        prefix = "User" if author_type == 'user' else "Agent"
        discussion_context += f"\n{prefix} ({author}): {content}..."
    
    # Count stats
    human_reply_count = len([r for r in post_data['replies'] if r.get('author_type') == 'user'])
    agent_reply_count = len([r for r in post_data['replies'] if r.get('author_type') == 'agent'])
    
    # LLM prompt - focused on "discovery that helps user's analysis"
    prompt = build_scout_trigger_prompt(
        user_direction=user_direction,
        discussion_context=discussion_context,
        human_reply_count=human_reply_count,
        agent_reply_count=agent_reply_count,
    )
    
    try:
        response = await client.chat.completions.create(
            model=settings.UTILITY_MODEL,
            messages=[
                {"role": "system", "content": SCOUT_TRIGGER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.8,
            max_completion_tokens=200
        )
        
        result_text = (response.choices[0].message.content or "").strip()
        logger.info(f"LLM decision raw: {result_text[:100]}...")
        
        # Parse JSON
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()
            
        decision = json.loads(result_text)
        
        # Apply 8+ threshold for high-quality discoveries only
        score = decision.get("relevance_score", 0)
        should_create = score >= 8
        
        # Logging
        logger.info(f"Scout Score: {score}/10 → {' CREATE' if should_create else ' SKIP'} | {decision.get('reason', 'N/A')[:80]}")
        
        return {
            "should_create": should_create,
            "reason": decision.get("reason", ""),
            "confidence": score / 10.0,
            "direction": decision.get("direction", ""),
            "relevance_score": score
        }
        
    except Exception as e:
        logger.error(f"LLM decision failed: {str(e)}")
        return {
            "should_create": False,
            "reason": f"LLM decision failed: {str(e)}",
            "confidence": 0.0
        }


def summarize_discussion(post_data: dict) -> str:
    """
    Summarize a post and its replies for context
    
    Args:
        post_data: Post object with 'content' and 'replies'
        
    Returns:
        String summary of the discussion
    """
    summary_lines = [
        f"Original post by {post_data['author']}:",
        f"  {post_data['content'][:150]}..." if len(post_data['content']) > 150 else f"  {post_data['content']}",
        f"\nDiscussion ({len(post_data['replies'])} replies):"
    ]
    
    # Include last 5 replies for context
    recent_replies = post_data['replies'][-5:] if len(post_data['replies']) > 5 else post_data['replies']
    
    for reply in recent_replies:
        author = reply['author']
        author_type = reply.get('author_type', 'user')
        content = reply['content'][:100]
        label = "User" if author_type == "user" else "Agent"
        summary_lines.append(f"  {label} {author}: {content}...")
    
    return "\n".join(summary_lines)


def extract_mention_request(text: str) -> Optional[str]:
    """
    Extract request text from agent mention
    
    Args:
        text: Text containing mention
        
    Returns:
        Extracted request or None
    """
    import re
    pattern = r'@\w+,?\s*(.+?)(?=[.!?]|@|$)'
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


async def should_add_proactive_visualization(agent_reply_content: str, agent_name: str) -> Dict[str, Any]:
    """
    Use LLM to decide if a visualization would enhance an agent's reply
    
    Args:
        agent_reply_content: Content of the agent's reply
        agent_name: Name of the agent who replied
        
    Returns:
        {
            "should_visualize": bool,
            "reason": str,
            "chart_description": str  # What kind of chart would be useful
        }
    """
    from openai import AsyncOpenAI
    from config import settings
    
    # Skip if reply is already from Visualization Expert
    if "Visualization" in agent_name or "visualiz" in agent_name.lower():
        return {
            "should_visualize": False,
            "reason": "Already from visualization agent",
            "chart_description": None
        }
    
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    
    prompt = build_auto_visualization_prompt(agent_reply_content)
    
    try:
        response = await client.chat.completions.create(
            model=settings.UTILITY_MODEL,
            messages=[
                {"role": "system", "content": AUTO_VISUALIZATION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_completion_tokens=150
        )
        
        result_text = (response.choices[0].message.content or "").strip()
        
        # Parse JSON
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()
            
        decision = json.loads(result_text)
        
        logger.info(f"Viz decision: {'ADD' if decision.get('should_visualize') else 'SKIP'}")
        if decision.get('should_visualize'):
            logger.info(f"Chart idea: {decision.get('chart_description', 'N/A')}")
        
        return decision
        
    except Exception as e:
        logger.error(f"Visualization decision failed: {str(e)}")
        return {
            "should_visualize": False,
            "reason": f"Decision failed: {str(e)}",
            "chart_description": None
        }


async def trigger_agent_collaboration(
    proactive_post_content: str,
    proactive_post_id: int,
    file_id: str,
    file_path: str
) -> list:
    """
    Trigger automatic agent collaboration on proactive posts
    
    When a proactive post is created with questions, automatically trigger
    2-3 specialist agents to provide initial responses.
    
    Args:
        proactive_post_content: Content of the proactive post
        file_id: Dataset file ID
        file_path: Path to dataset file
        
    Returns:
        List of agent reply data
    """
    from agents_sdk import run_agent_analysis, analyst_agent, visualizer_agent, insight_agent
    from utils.logger import logger
    
    replies = []
    
    # Extract first question from proactive post (if any)
    import re
    questions = re.findall(r'[1-3]️⃣\s+(.+?)(?=\n|$)', proactive_post_content)
    
    if not questions:
        logger.info("No questions found in proactive post, skipping auto-collaboration")
        return replies
    
    first_question = questions[0]
    logger.info(f"Auto-collaboration triggered for question: {first_question[:50]}...")
    
    # Agent 1: Statistics Agent provides numbers
    try:
        logger.info("Triggering Statistics Agent...")
        stats_result = await run_agent_analysis(
            message=first_question,
            file_path=file_path,
            file_id=file_id,
            agent=analyst_agent,
            post_id=proactive_post_id,
            ws_context="reply",
        )
        
        replies.append({
            "author": stats_result.get('agent', 'Statistical Analyst Agent'),
            "author_type": "agent",
            "author_role": stats_result.get('agent_role', 'statistics'),
            "content": stats_result.get('content', ''),
            "visualization": stats_result.get('visualization')
        })
        logger.info("Statistics Agent reply generated")
    except Exception as e:
        logger.error(f"Statistics Agent failed: {str(e)}")
    
    # Agent 2: Visualization Agent (if question asks for visual)
    if any(word in first_question.lower() for word in ['show', 'chart', 'graph', 'visualize', 'plot']):
        try:
            logger.info("Triggering Visualization Agent...")
            viz_result = await run_agent_analysis(
                message=f"Create a visualization for: {first_question}",
                file_path=file_path,
                file_id=file_id,
                agent=visualizer_agent,
                post_id=proactive_post_id,
                ws_context="reply",
            )
            
            replies.append({
                "author": viz_result.get('agent', 'Visualization Expert Agent'),
                "author_type": "agent",
                "author_role": viz_result.get('agent_role', 'visualization'),
                "content": viz_result.get('content', ''),
                "visualization": viz_result.get('visualization')
            })
            logger.info("Visualization Agent reply generated")
        except Exception as e:
            logger.error(f"Visualization Agent failed: {str(e)}")
    
    return replies


# Global instance
proactive_trigger = ProactiveAgentTrigger()


# ============ DATA SCOPE EXTRACTION ============

async def extract_data_scope(user_message: str, dataset_columns: list) -> Dict[str, Any]:
    """
    Extract data scope/filters from user message using LLM.
    
    This helps agents focus on the specific subset of data the user is interested in.
    
    Args:
        user_message: The user's message/question
        dataset_columns: List of column names in the dataset
        
    Returns:
        Dictionary with extracted scope filters
    """
    from openai import OpenAI
    
    client = OpenAI()
    
    # Create a prompt for scope extraction — strict: only explicit filter intent
    prompt = f"""Does the user EXPLICITLY ask to filter or restrict data to a specific subset?

User message: "{user_message}"

Available columns: {dataset_columns}

RULES:
- Return {{}} unless the user explicitly asks to filter, restrict, focus, or limit the dataset to a subset.
- Use only columns from the available column list. Do not invent column names.
- Include a filter value only when the user explicitly names that value as the desired subset.
- Mentioning a time, entity, or category as part of a question is not enough to create a filter.
- For trends, trajectories, timelines, or other time-based analysis, do not create a time filter unless the user explicitly restricts the period.
- A non-time subset filter may be returned only when the user's wording clearly scopes the analysis to that subset.
- When in doubt, return {{}}.

Return ONLY valid JSON, no explanation.

JSON output:"""

    try:
        response = client.chat.completions.create(
            model=settings.UTILITY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=200,
            temperature=0.1
        )
        
        result_text = (response.choices[0].message.content or "").strip()
        
        # Clean up response (remove markdown code blocks if present)
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
        result_text = result_text.strip()
        
        scope = json.loads(result_text)
        
        if scope:
            logger.info(f"Extracted data scope: {scope}")
        
        return scope
        
    except Exception as e:
        logger.warning(f"Failed to extract data scope: {str(e)}")
        return {}


def build_scope_sql_condition(data_scope: Dict[str, Any]) -> str:
    """
    Build SQL WHERE condition from data scope.
    
    Args:
        data_scope: Dictionary of scope filters
        
    Returns:
        SQL WHERE clause string (without 'WHERE' keyword)
    """
    if not data_scope:
        return ""
    
    conditions = []
    
    for column, value in data_scope.items():
        if isinstance(value, dict):
            # Range filter
            if "min" in value and "max" in value:
                conditions.append(f'"{column}" >= {value["min"]} AND "{column}" <= {value["max"]}')
            elif "min" in value:
                conditions.append(f'"{column}" >= {value["min"]}')
            elif "max" in value:
                conditions.append(f'"{column}" <= {value["max"]}')
        elif isinstance(value, list):
            # IN filter
            escaped_values = [f"'{v}'" if isinstance(v, str) else str(v) for v in value]
            conditions.append(f'"{column}" IN ({", ".join(escaped_values)})')
        elif isinstance(value, str):
            # Exact match or LIKE
            conditions.append(f'"{column}" = \'{value}\'')
        elif isinstance(value, (int, float)):
            conditions.append(f'"{column}" = {value}')
    
    return " AND ".join(conditions)
