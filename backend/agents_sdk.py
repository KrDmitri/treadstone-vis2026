"""
OpenAI Agents SDK based Multi-Agent System

This module uses OpenAI Agents SDK to implement a multi-agent collaboration system
for data analysis with SNS-style interactions.
"""

import os
import json
import pandas as pd
import duckdb
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from contextvars import ContextVar

# Import Agents SDK
from agents import Agent, Runner, function_tool, SQLiteSession, WebSearchTool, ModelSettings

# Import config for agent settings
from config import settings
from prompts import (
    AGENT_INSTRUCTIONS,
    REQUEST_ROUTER_INSTRUCTIONS,
    SUMMARY_WRITER_INSTRUCTIONS,
    NEXT_STEP_GENERATOR_INSTRUCTIONS,
    build_summary_next_steps_prompt,
    COUNTERPOINT_EVALUATOR_INSTRUCTIONS,
    DISCUSSION_RELEVANCE_EVALUATOR_INSTRUCTIONS,
    build_counterpoint_eval_prompt,
    build_counterpoint_prompt,
    build_discussion_image_notice,
    build_discussion_relevance_prompt,
    build_discussion_reply_prompt,
    build_mention_intent_prompt,
    build_proactive_analysis_message,
    build_semantic_tagging_prompt,
    build_semantic_connection_prompt,
    SEMANTIC_CONNECTION_SYSTEM_PROMPT,
)
from context_management import ContextBlock, assemble_context, estimate_tokens, truncate_text, trim_json_list_payload

# Load environment variables
load_dotenv()

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ==================== Global Dataset Storage ====================

# Store uploaded datasets in memory for agent querying
uploaded_datasets: Dict[str, Dict[str, Any]] = {}

# Server startup timestamp - used to create unique session IDs per server instance
# This ensures sessions are NOT reused after server restart (prevents context buildup)
import time
SERVER_START_TIME = str(int(time.time()))

# ==================== Tool Output Limits ====================
# Limits for tool outputs to prevent context window overflow
MAX_TOOL_OUTPUT_SIZE = 500_000  # 500KB default — reduced dynamically on context overflow
MAX_TOOL_ROWS = 5000            # 5000 rows maximum

# ==================== Post Reference Limits ====================
# Limits for #N post references to prevent context window overflow
MAX_REFERENCE_POSTS = 5               # Maximum 5 post references
REFERENCE_CONTEXT_TOKEN_BUDGET = 8_000
REFERENCE_POST_TOKEN_BUDGET = 1_200
REFERENCE_POST_BODY_TOKEN_BUDGET = 160
REFERENCE_REPLY_TOKEN_BUDGET = 120
CONVERSATION_DATA_TOKEN_BUDGET = 3_500
CONVERSATION_MESSAGE_TOKEN_BUDGET = 80
TEXT_FILE_TOKEN_BUDGET = 2_500
AGENT_REFERENCE_TOKEN_BUDGET = 3_000
DISCUSSION_CONTEXT_TOKEN_BUDGET = 650
DISCUSSION_CONTEXT_RETRY_BUDGETS = (650, 350, 180)

# Keep short UI-facing generations from reserving the model's full output window.
# Without explicit caps, discussion replies can reserve very large TPM budgets.
CORE_AGENT_MODEL_SETTINGS = ModelSettings(max_tokens=2500)
DISCUSSION_REPLY_MODEL_SETTINGS = ModelSettings(max_tokens=1200)
SUMMARY_MODEL_SETTINGS = ModelSettings(max_tokens=2500)
NEXT_STEP_MODEL_SETTINGS = ModelSettings(max_tokens=1600)
UTILITY_JSON_MODEL_SETTINGS = ModelSettings(max_tokens=300)

# In-memory cache for SQLiteSession objects (keyed by file_id)
active_sessions: Dict[str, SQLiteSession] = {}


def _get_session_db_path() -> str:
    """Return a writable SQLite session DB path for both local and Docker runs."""
    db_path = Path(settings.SESSION_DB_PATH)
    if not db_path.is_absolute():
        db_path = settings.UPLOAD_DIR / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)

# ==================== Data Scope (Context-Aware Filtering) ====================
# Current data scope for filtering queries (set per analysis run)
current_data_scope: Dict[str, Any] = {}

# ==================== Client Context (Thread-Safe) ====================
# Current client_id for isolation - uses contextvars for async safety
current_client_id: ContextVar[str] = ContextVar('current_client_id', default='')

# ==================== Visualization Registry (Duplicate Prevention) ====================
# Session-level registry to track generated visualizations and prevent duplicates
# Uses data hash for comparison to detect semantically identical charts
# Key: (chart_type, data_hash) tuple
# Value: dict with title, timestamp, count, fields
visualization_registry: Dict[tuple, Dict[str, Any]] = {}

def _get_data_hash(data_json: str) -> str:
    """
    Generate a hash from the actual data values.
    This allows detecting duplicate charts even with different field names.
    """
    import hashlib
    try:
        data = json.loads(data_json) if isinstance(data_json, str) else data_json
        # Sort and stringify the data to ensure consistent hashing
        # Only hash first 10 rows to balance accuracy and performance
        sample = data[:10] if len(data) > 10 else data
        # Sort keys within each dict for consistent ordering
        normalized = [dict(sorted(row.items())) for row in sample]
        data_str = json.dumps(normalized, sort_keys=True)
        return hashlib.md5(data_str.encode()).hexdigest()[:12]
    except:
        return ""

def _get_viz_key(chart_type: str, data_hash: str) -> tuple:
    """Generate a unique key for visualization registry lookup."""
    return (chart_type.lower().strip(), data_hash)

def _is_duplicate_visualization(chart_type: str, x_field: str, y_field: str, title: str, data_json: str = "") -> bool:
    """
    Check if a similar visualization has already been generated in this session.
    Uses data hash for comparison to detect semantically identical charts.
    Returns True if duplicate, False if new.
    """
    from utils.logger import logger
    
    # Generate data hash for comparison
    data_hash = _get_data_hash(data_json)
    
    if not data_hash:
        # Fallback to field-based key if data hash fails
        key = (chart_type.lower().strip(), x_field.lower().strip(), y_field.lower().strip())
    else:
        key = _get_viz_key(chart_type, data_hash)
    
    if key in visualization_registry:
        existing = visualization_registry[key]
        existing["count"] += 1
        logger.warning(f"Duplicate visualization detected: {chart_type} chart with same data")
        logger.warning(f"Original: '{existing['title']}' ({existing['fields']})")
        logger.warning(f"Duplicate attempt: '{title}' ({x_field} vs {y_field}) - blocked #{existing['count']}")
        return True
    
    return False

def _register_visualization(chart_type: str, x_field: str, y_field: str, title: str, data_json: str = ""):
    """Register a new visualization in the session registry."""
    from utils.logger import logger
    
    data_hash = _get_data_hash(data_json)
    
    if not data_hash:
        key = (chart_type.lower().strip(), x_field.lower().strip(), y_field.lower().strip())
    else:
        key = _get_viz_key(chart_type, data_hash)
    
    visualization_registry[key] = {
        "title": title,
        "fields": f"{x_field} vs {y_field}",
        "data_hash": data_hash,
        "timestamp": datetime.now().isoformat(),
        "count": 1
    }
    logger.info(f"Registered new visualization: {chart_type} ({x_field} vs {y_field}) - '{title}' [hash: {data_hash}]")

def clear_visualization_registry():
    """Clear the visualization registry (call on session reset)."""
    global visualization_registry
    visualization_registry.clear()
    from utils.logger import logger
    logger.info("Visualization registry cleared")

# ==================== Vision API Support ====================
# Store image data for multimodal input (Vision API)
pending_image_data: Optional[str] = None


# ==================== Language Detection ====================

def detect_language(text: str) -> str:
    """Detect language from text using Unicode character ranges."""
    for ch in text:
        if '\uAC00' <= ch <= '\uD7A3' or '\u3131' <= ch <= '\u318E':
            return "Korean"
    return "English"


def resolve_language(client_id: str = None, message: str = None) -> str:
    """Resolve effective language: use setting if explicit, else auto-detect from message."""
    lang = "English"
    if client_id:
        try:
            from services.client_store import get_client_store
            store = get_client_store(client_id)
            lang = store.language
        except Exception:
            pass
    
    if lang == "Auto" and message:
        lang = detect_language(message)
    elif lang == "Auto":
        lang = "English"
    
    return lang


# ==================== Helper Functions ====================

def _check_output_size(result_json: str, operation: str) -> str:
    """
    Smart truncation: if output exceeds MAX_TOOL_OUTPUT_SIZE, return a meaningful
    summary instead of a generic error so the LLM still gets actionable data.
    """
    from utils.logger import logger

    output_size = len(result_json)
    if output_size <= MAX_TOOL_OUTPUT_SIZE:
        return result_json

    logger.warning(f"Tool output too large: {output_size:,} bytes | operation={operation} — applying smart truncation")

    try:
        data = json.loads(result_json)
    except Exception:
        return json.dumps({
            "warning": "Result truncated (unparseable JSON)",
            "size_bytes": output_size,
            "suggestion": "Add filters or use aggregate operations to reduce result size."
        })

    # ── keyword_count ─────────────────────────────────────────────────────────
    if operation == "keyword_count":
        raw_results = data.get("results", {})

        if isinstance(raw_results, dict):
            sorted_items = sorted(raw_results.items(), key=lambda x: x[1], reverse=True)
            total_unique = len(sorted_items)
            total_mentions = sum(raw_results.values())
            top_8 = dict(sorted_items[:8])
            return json.dumps({
                "operation": "keyword_count",
                "column": data.get("column"),
                "total_unique_keywords": total_unique,
                "total_mentions": total_mentions,
                "top_8_keywords": top_8,
                "note": f"Output truncated. Showing top 8 of {total_unique} unique keywords."
            }, indent=2, default=str)

        elif isinstance(raw_results, list):
            total_rows = len(raw_results)
            # Keep up to top 5 keywords × all their group_by values (preserves completeness for charting)
            kw_counts = {}
            for r in raw_results:
                kw = r.get("keyword", "")
                kw_counts[kw] = kw_counts.get(kw, 0) + r.get("count", 0)
            top_kws = sorted(kw_counts, key=kw_counts.get, reverse=True)[:5]
            kept_rows = [r for r in raw_results if r.get("keyword", "") in top_kws]
            return json.dumps({
                "operation": "keyword_count",
                "column": data.get("column"),
                "group_by": data.get("group_by"),
                "total_rows": total_rows,
                "kept_keywords": top_kws,
                "results": kept_rows,
                "note": f"Output truncated. Keeping top {len(top_kws)} keywords with all their time periods ({len(kept_rows)} rows of {total_rows})."
            }, indent=2, default=str)

    # ── trend / trend_compare ─────────────────────────────────────────────────
    elif operation in ("trend", "trend_compare"):
        results = data.get("results", [])
        total_rows = len(results)
        if total_rows > 500:
            step = max(1, total_rows // 500)
            results = results[::step]
        return json.dumps({
            **data,
            "results": results,
            "note": f"Result sampled to {len(results)} of {total_rows} rows for context efficiency."
        }, indent=2, default=str)

    # ── raw ───────────────────────────────────────────────────────────────────
    elif operation == "raw":
        rows = data.get("data", [])
        total_rows = len(rows)
        cols = list(rows[0].keys()) if rows else []
        return json.dumps({
            "operation": "raw",
            "total_rows_in_result": total_rows,
            "columns": cols,
            "first_30_rows": rows[:30],
            "note": f"Showing first 30 of {total_rows} rows. Use filters or aggregates for full analysis."
        }, indent=2, default=str)

    # ── generic fallback ──────────────────────────────────────────────────────
    results = data.get("results", [])
    if isinstance(results, list):
        truncated = results[:30]
        original_count = len(results)
    elif isinstance(results, dict):
        items = sorted(results.items(), key=lambda x: x[1] if isinstance(x[1], (int, float)) else 0, reverse=True)
        truncated = dict(items[:30])
        original_count = len(results)
    else:
        truncated = results
        original_count = output_size

    return json.dumps({
        **{k: v for k, v in data.items() if k != "results"},
        "results": truncated,
        "note": f"Output truncated. Showing top 30 of {original_count} entries."
    }, indent=2, default=str)


def load_dataset(file_path: str, file_id: str) -> bool:
    """
    Load CSV dataset for agent querying (uses DuckDB if available, Pandas as fallback)
    
    Args:
        file_path: Path to CSV file
        file_id: Unique identifier for this dataset
        
    Returns:
        True if successful, False otherwise
    """
    from utils.logger import logger
    from pathlib import Path
    
    try:
        # Check if DuckDB file exists
        db_path = Path(file_path).parent / f"{Path(file_path).stem}.duckdb"
        
        if db_path.exists():
            # Use DuckDB for efficient querying
            logger.info(f"Using DuckDB for dataset queries: {db_path}")
            
            # Get metadata from DuckDB without loading full dataset
            conn = duckdb.connect(str(db_path), read_only=True)
            row_count = conn.execute("SELECT COUNT(*) FROM data").fetchone()[0]
            # Get column names from PRAGMA table_info (col[1] is the name, col[0] is the cid)
            columns = [str(col[1]) for col in conn.execute("PRAGMA table_info(data)").fetchall()]
            conn.close()
            
            uploaded_datasets[file_id] = {
                "db_path": str(db_path),
                "file_path": file_path,
                "loaded_at": datetime.now().isoformat(),
                "rows": row_count,
                "columns": columns,
                "query_engine": "duckdb"
            }
            
            logger.info(f"DuckDB dataset ready: {row_count:,} rows, {len(columns)} columns")
            logger.info(f"Columns: {', '.join(columns[:10])}{'...' if len(columns) > 10 else ''}")
            logger.info(f"Using efficient DuckDB queries (no memory load)")
            
        else:
            # Fallback to Pandas (for older datasets)
            logger.info(f"Loading dataset into memory (Pandas): {file_path}")
            df = pd.read_csv(file_path)
            
            uploaded_datasets[file_id] = {
                "df": df,
                "file_path": file_path,
                "loaded_at": datetime.now().isoformat(),
                "rows": len(df),
                "columns": list(df.columns),
                "query_engine": "pandas"
            }
            
            logger.info(f"Dataset loaded: {len(df):,} rows, {len(df.columns)} columns")
            logger.info(f"Columns: {', '.join(df.columns[:10])}{'...' if len(df.columns) > 10 else ''}")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to load dataset: {str(e)}")
        return False


# ==================== Tool Functions ====================

@function_tool
def get_dataset_info(file_id: str) -> str:
    """
    Get comprehensive information about the dataset structure and contents.
    
    Args:
        file_id: Unique identifier of the dataset to query
        
    Returns:
        JSON string containing dataset metadata, columns, types, and sample data
    """
    from utils.logger import logger
    try:
        if file_id not in uploaded_datasets:
            return json.dumps({"error": f"Dataset with ID '{file_id}' not found. Available datasets: {list(uploaded_datasets.keys())}"})
        
        dataset = uploaded_datasets[file_id]
        
        # DuckDB mode
        if dataset.get("query_engine") == "duckdb":
            db_path = dataset["db_path"]
            conn = duckdb.connect(str(db_path), read_only=True)
            
            # Get column info
            table_info = conn.execute("PRAGMA table_info(data)").fetchdf()
            columns = table_info['name'].tolist()
            column_types = dict(zip(table_info['name'], table_info['type']))
            
            # Get sample data
            sample_head = conn.execute("SELECT * FROM data LIMIT 5").fetchdf().to_dict(orient='records')
            sample_tail = conn.execute("SELECT * FROM data ORDER BY rowid DESC LIMIT 3").fetchdf().to_dict(orient='records')
            
            conn.close()
            
            info = {
                "success": True,
                "rows": dataset["rows"],
                "columns": columns,
                "column_types": column_types,
                "sample_head": sample_head,
                "sample_tail": sample_tail,
                "loaded_at": dataset["loaded_at"],
                "query_engine": "duckdb"
            }
        
        # Pandas fallback mode
        else:
            df = dataset["df"]
            
            info = {
                "success": True,
                "rows": len(df),
                "columns": list(df.columns),
                "column_types": df.dtypes.astype(str).to_dict(),
                "missing_values": df.isnull().sum().to_dict(),
                "numeric_columns": list(df.select_dtypes(include=['number']).columns),
                "text_columns": list(df.select_dtypes(include=['object']).columns),
                "sample_head": df.head(5).to_dict(orient='records'),
                "sample_tail": df.tail(3).to_dict(orient='records'),
                "loaded_at": dataset["loaded_at"],
                "query_engine": "pandas"
            }
        
        return json.dumps(info, indent=2, default=str)
        
    except Exception as e:
        logger.error(f"get_dataset_info failed: {str(e)}", exc_info=True)
        return json.dumps({"error": f"Failed to get dataset info: {str(e)}"})


@function_tool
def get_column_summary(file_id: str, column: str) -> str:
    """
    Get detailed statistical summary for a specific column.
    
    Args:
        file_id: Unique identifier of the dataset
        column: Name of the column to summarize
        
    Returns:
        JSON string with column statistics (numeric: mean/std/quartiles, categorical: value counts)
    """
    from utils.logger import logger
    try:
        if file_id not in uploaded_datasets:
            return json.dumps({"error": f"Dataset '{file_id}' not found"})
        
        dataset = uploaded_datasets[file_id]
        
        # DuckDB mode
        if dataset.get("query_engine") == "duckdb":
            db_path = dataset["db_path"]
            conn = duckdb.connect(str(db_path), read_only=True)
            
            # Check if column exists
            columns_info = conn.execute("PRAGMA table_info(data)").fetchdf()
            if column not in columns_info['name'].tolist():
                conn.close()
                return json.dumps({
                    "error": f"Column '{column}' not found",
                    "available_columns": columns_info['name'].tolist()
                })
            
            # Get column type
            col_type = columns_info[columns_info['name'] == column]['type'].iloc[0]
            
            # Determine if numeric or categorical
            is_numeric = 'INT' in col_type.upper() or 'DOUBLE' in col_type.upper() or 'FLOAT' in col_type.upper() or 'NUMERIC' in col_type.upper() or 'DECIMAL' in col_type.upper()
            
            # Get descriptive statistics (different queries for numeric vs text)
            if is_numeric:
                desc_stats = conn.execute(f"""
                    SELECT 
                        COUNT(*) as total_count,
                        COUNT("{column}") as non_null_count,
                        COUNT(DISTINCT "{column}") as unique_count,
                        MIN("{column}") as min_val,
                        MAX("{column}") as max_val,
                        AVG(CAST("{column}" AS DOUBLE)) as mean_val,
                        STDDEV_POP(CAST("{column}" AS DOUBLE)) as std_val
                    FROM data
                """).fetchone()
            else:
                desc_stats = conn.execute(f"""
                    SELECT 
                        COUNT(*) as total_count,
                        COUNT("{column}") as non_null_count,
                        COUNT(DISTINCT "{column}") as unique_count,
                        MIN("{column}") as min_val,
                        MAX("{column}") as max_val,
                        NULL as mean_val,
                        NULL as std_val
                    FROM data
                """).fetchone()
            
            # Get top 10 values
            top_values_df = conn.execute(f"""
                SELECT "{column}", COUNT(*) as count 
                FROM data 
                GROUP BY "{column}" 
                ORDER BY count DESC 
                LIMIT 10
            """).fetchdf()
            
            conn.close()
            
            if is_numeric:
                summary = {
                    "column": column,
                    "type": "numeric",
                    "count": int(desc_stats[1]),
                    "missing": int(desc_stats[0] - desc_stats[1]),
                    "mean": float(desc_stats[5]) if desc_stats[5] is not None else None,
                    "std": float(desc_stats[6]) if desc_stats[6] is not None else None,
                    "min": float(desc_stats[3]) if desc_stats[3] is not None else None,
                    "max": float(desc_stats[4]) if desc_stats[4] is not None else None,
                    "unique": int(desc_stats[2])
                }
            else:
                summary = {
                    "column": column,
                    "type": "categorical",
                    "count": int(desc_stats[1]),
                    "missing": int(desc_stats[0] - desc_stats[1]),
                    "unique": int(desc_stats[2]),
                    "top_10_values": dict(zip(top_values_df[column].astype(str), top_values_df['count']))
                }
        
        # Pandas fallback mode
        else:
            df = dataset["df"]
            
            if column not in df.columns:
                return json.dumps({
                    "error": f"Column '{column}' not found",
                    "available_columns": list(df.columns)
                })
            
            col_data = df[column]
            
            # Check if numeric or categorical
            if pd.api.types.is_numeric_dtype(col_data):
                summary = {
                    "column": column,
                    "type": "numeric",
                    "count": int(col_data.count()),
                    "missing": int(col_data.isnull().sum()),
                    "mean": float(col_data.mean()) if col_data.count() > 0 else None,
                    "std": float(col_data.std()) if col_data.count() > 0 else None,
                    "min": float(col_data.min()) if col_data.count() > 0 else None,
                    "max": float(col_data.max()) if col_data.count() > 0 else None,
                    "median": float(col_data.median()) if col_data.count() > 0 else None,
                    "quartiles": {
                        "25%": float(col_data.quantile(0.25)) if col_data.count() > 0 else None,
                        "50%": float(col_data.quantile(0.50)) if col_data.count() > 0 else None,
                        "75%": float(col_data.quantile(0.75)) if col_data.count() > 0 else None
                    }
                }
            else:
                summary = {
                    "column": column,
                    "type": "categorical",
                    "count": int(col_data.count()),
                    "missing": int(col_data.isnull().sum()),
                    "unique": int(col_data.nunique()),
                    "top_10_values": col_data.value_counts().head(10).to_dict()
                }
        
        return json.dumps(summary, indent=2, default=str)
        
    except Exception as e:
        logger.error(f"get_column_summary failed: {str(e)}", exc_info=True)
        return json.dumps({"error": f"Failed to summarize column: {str(e)}"})


# ==================== Query Helper Functions ====================

def _parse_filter_value(col: str, val) -> list:
    """
    Parse filter value and return SQL conditions.
    Supports:
    - Exact match: "Manhattan" → column = 'Manhattan'
    - Range: "40-100" → column >= 40 AND column <= 100
    - Min only: "10-" or ">=10" or ">10" → column >= 10
    - Max only: "-100" or "<=100" or "<100" → column <= 100
    - Comparison operators: ">50", ">=50", "<100", "<=100"
    """
    conditions = []
    val_str = str(val).strip()
    
    # Check for comparison operators first
    if val_str.startswith('>='):
        try:
            num = float(val_str[2:].strip())
            conditions.append(f'"{col}" >= {num}')
            return conditions
        except ValueError:
            pass
    elif val_str.startswith('<='):
        try:
            num = float(val_str[2:].strip())
            conditions.append(f'"{col}" <= {num}')
            return conditions
        except ValueError:
            pass
    elif val_str.startswith('>') and not val_str.startswith('>='):
        try:
            num = float(val_str[1:].strip())
            conditions.append(f'"{col}" > {num}')
            return conditions
        except ValueError:
            pass
    elif val_str.startswith('<') and not val_str.startswith('<='):
        try:
            num = float(val_str[1:].strip())
            conditions.append(f'"{col}" < {num}')
            return conditions
        except ValueError:
            pass
    
    # Check for range format: "min-max", "min-", "-max"
    if '-' in val_str:
        parts = val_str.split('-', 1)
        
        # Handle negative numbers as values, not ranges.
        if len(parts) == 2:
            min_part, max_part = parts[0].strip(), parts[1].strip()
            
            # Closed numeric range.
            if min_part and max_part:
                try:
                    min_val = float(min_part)
                    max_val = float(max_part)
                    conditions.append(f'"{col}" >= {min_val}')
                    conditions.append(f'"{col}" <= {max_val}')
                    return conditions
                except ValueError:
                    pass  # Not a numeric range, fall through to LIKE
            
            # Open-ended minimum range.
            elif min_part and not max_part:
                try:
                    min_val = float(min_part)
                    conditions.append(f'"{col}" >= {min_val}')
                    return conditions
                except ValueError:
                    pass
            
            # Open-ended maximum range.
            elif not min_part and max_part:
                try:
                    max_val = float(max_part)
                    conditions.append(f'"{col}" <= {max_val}')
                    return conditions
                except ValueError:
                    pass
    
    # Default: LIKE match for text values
    conditions.append(f'CAST("{col}" AS VARCHAR) LIKE \'%{val_str}%\'')
    return conditions


def _query_with_duckdb(db_path: str, operation: str, column: str, filters: str, group_by: str, limit: int) -> str:
    """Execute query using DuckDB (efficient for large datasets)"""
    from utils.logger import logger
    
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        
        # Build WHERE clause from filters with range support
        where_clause = ""
        if filters:
            try:
                filter_dict = json.loads(filters)
                conditions = []
                for col, val in filter_dict.items():
                    # Use enhanced filter parsing
                    col_conditions = _parse_filter_value(col, val)
                    conditions.extend(col_conditions)
                if conditions:
                    where_clause = "WHERE " + " AND ".join(conditions)
                    logger.info(f"[FILTER] Generated WHERE clause: {where_clause}")
            except json.JSONDecodeError:
                conn.close()
                return json.dumps({"error": "Invalid filters JSON format"})
        
        # Execute operation-specific SQL
        if operation == "count":
            if group_by:
                sql = f'SELECT "{group_by}", COUNT(*) as count FROM data {where_clause} GROUP BY "{group_by}" ORDER BY count DESC LIMIT {limit}'
                result = conn.execute(sql).fetchdf()
                conn.close()
                result_json = json.dumps({"operation": "count", "group_by": group_by, "results": result.set_index(group_by)['count'].to_dict()}, indent=2)
                return _check_output_size(result_json, "count")
            else:
                sql = f"SELECT COUNT(*) as total FROM data {where_clause}"
                total = conn.execute(sql).fetchone()[0]
                conn.close()
                return json.dumps({"operation": "count", "total_rows": total})
        
        elif operation == "sum" and column:
            if group_by:
                sql = f'SELECT "{group_by}", SUM("{column}") as sum FROM data {where_clause} GROUP BY "{group_by}" ORDER BY sum DESC LIMIT {limit}'
                result = conn.execute(sql).fetchdf()
                conn.close()
                result_json = json.dumps({"operation": "sum", "column": column, "group_by": group_by, "results": result.set_index(group_by)['sum'].to_dict()}, indent=2)
                return _check_output_size(result_json, "sum")
            else:
                sql = f'SELECT SUM("{column}") as total FROM data {where_clause}'
                total = conn.execute(sql).fetchone()[0]
                conn.close()
                return json.dumps({"operation": "sum", "column": column, "total": float(total) if total else 0})
        
        elif operation == "mean" and column:
            if group_by:
                sql = f'SELECT "{group_by}", AVG("{column}") as avg FROM data {where_clause} GROUP BY "{group_by}" ORDER BY avg DESC LIMIT {limit}'
                result = conn.execute(sql).fetchdf()
                conn.close()
                result_json = json.dumps({"operation": "mean", "column": column, "group_by": group_by, "results": result.set_index(group_by)['avg'].to_dict()}, indent=2)
                return _check_output_size(result_json, "mean")
            else:
                sql = f'SELECT AVG("{column}") as avg FROM data {where_clause}'
                avg = conn.execute(sql).fetchone()[0]
                conn.close()
                return json.dumps({"operation": "mean", "column": column, "average": float(avg) if avg else 0})
        
        elif operation == "unique" and column:
            sql = f'SELECT "{column}", COUNT(*) as count FROM data {where_clause} GROUP BY "{column}" ORDER BY count DESC LIMIT {limit}'
            result = conn.execute(sql).fetchdf()
            conn.close()
            result_json = json.dumps({"operation": "unique", "column": column, "results": result.set_index(column)['count'].to_dict()}, indent=2)
            return _check_output_size(result_json, "unique")
        
        elif operation == "trend" and column and group_by:
            sql = f'SELECT "{group_by}", COUNT(*) as count, SUM("{column}") as sum, AVG("{column}") as mean FROM data {where_clause} GROUP BY "{group_by}" ORDER BY "{group_by}" LIMIT {limit}'
            result = conn.execute(sql).fetchdf()
            conn.close()
            result_json = json.dumps({"operation": "trend", "column": column, "group_by": group_by, "results": result.to_dict(orient='records')}, indent=2)
            return _check_output_size(result_json, "trend")
        
        elif operation == "top" and column:
            if group_by:
                sql = f'SELECT "{group_by}", "{column}" FROM data {where_clause} ORDER BY "{column}" DESC LIMIT {limit}'
            else:
                sql = f'SELECT "{column}" FROM data {where_clause} ORDER BY "{column}" DESC LIMIT {limit}'
            result = conn.execute(sql).fetchdf()
            conn.close()
            result_json = json.dumps({"operation": "top", "column": column, "results": result.to_dict(orient='records')}, indent=2)
            return _check_output_size(result_json, "top")
        
        elif operation == "keyword_count" and column:
            # Split delimited keywords and count individual keyword frequencies
            # Auto-detect delimiter: semicolon, pipe, or comma
            sample_sql = f'SELECT "{column}" FROM data WHERE "{column}" IS NOT NULL LIMIT 50'
            sample_vals = conn.execute(sample_sql).fetchdf()[column].tolist()
            sample_text = " ".join(str(v) for v in sample_vals)
            if sample_text.count(';') >= 3:
                delimiter = ';'
            elif sample_text.count('|') >= 3:
                delimiter = '|'
            else:
                delimiter = ','

            if group_by:
                # Step 1: get top keywords overall (to filter noise)
                top_kw_sql = f"""
                    SELECT LOWER(TRIM(keyword)) as keyword, COUNT(*) as total
                    FROM data, UNNEST(string_split("{column}", '{delimiter}')) AS t(keyword)
                    {where_clause}
                    GROUP BY LOWER(TRIM(keyword))
                    HAVING LOWER(TRIM(keyword)) != '' AND LOWER(TRIM(keyword)) IS NOT NULL AND COUNT(*) >= 2
                    ORDER BY total DESC
                    LIMIT 8
                """
                top_kws = conn.execute(top_kw_sql).fetchdf()['keyword'].tolist()

                if top_kws:
                    kw_list = ", ".join(f"'{kw}'" for kw in top_kws)
                    # CROSS JOIN top keywords × all group_by values, then LEFT JOIN actual counts
                    sql = f"""
                        WITH actual AS (
                            SELECT LOWER(TRIM(keyword)) as keyword, "{group_by}", COUNT(*) as count
                            FROM data, UNNEST(string_split("{column}", '{delimiter}')) AS t(keyword)
                            {where_clause}
                            GROUP BY LOWER(TRIM(keyword)), "{group_by}"
                            HAVING LOWER(TRIM(keyword)) IN ({kw_list})
                        ),
                        all_groups AS (
                            SELECT DISTINCT "{group_by}" FROM data WHERE "{group_by}" IS NOT NULL
                        ),
                        top_keywords AS (
                            SELECT UNNEST([{kw_list}]) as keyword
                        ),
                        grid AS (
                            SELECT k.keyword, g."{group_by}"
                            FROM top_keywords k CROSS JOIN all_groups g
                        )
                        SELECT grid.keyword, grid."{group_by}", COALESCE(actual.count, 0) as count
                        FROM grid
                        LEFT JOIN actual ON grid.keyword = actual.keyword AND grid."{group_by}" = actual."{group_by}"
                        ORDER BY grid."{group_by}", grid.keyword
                    """
                else:
                    sql = f"""
                        SELECT LOWER(TRIM(keyword)) as keyword, "{group_by}", COUNT(*) as count
                        FROM data, UNNEST(string_split("{column}", '{delimiter}')) AS t(keyword)
                        {where_clause}
                        GROUP BY LOWER(TRIM(keyword)), "{group_by}"
                        HAVING LOWER(TRIM(keyword)) != '' AND LOWER(TRIM(keyword)) IS NOT NULL
                        ORDER BY count DESC
                        LIMIT {limit}
                    """
                result = conn.execute(sql).fetchdf()
                conn.close()
                result_json = json.dumps({
                    "operation": "keyword_count",
                    "column": column,
                    "group_by": group_by,
                    "total_keywords": len(result),
                    "results": result.to_dict(orient='records')
                }, indent=2, default=str)
            else:
                sql = f"""
                    SELECT LOWER(TRIM(keyword)) as keyword, COUNT(*) as count
                    FROM data, UNNEST(string_split("{column}", '{delimiter}')) AS t(keyword)
                    {where_clause}
                    GROUP BY LOWER(TRIM(keyword))
                    HAVING LOWER(TRIM(keyword)) != '' AND LOWER(TRIM(keyword)) IS NOT NULL
                    ORDER BY count DESC
                    LIMIT {limit}
                """
                result = conn.execute(sql).fetchdf()
                conn.close()
                result_json = json.dumps({
                    "operation": "keyword_count",
                    "column": column,
                    "total_keywords": len(result),
                    "results": dict(zip(result['keyword'], result['count'].astype(int)))
                }, indent=2, default=str)
            return _check_output_size(result_json, "keyword_count")
        
        elif operation == "trend_compare" and column and group_by:
            # Multi-series trend: group_by should be comma-separated.
            # First column = time axis, second column = series grouping
            group_cols = [g.strip() for g in group_by.split(',')]
            if len(group_cols) < 2:
                conn.close()
                return json.dumps({"error": "trend_compare requires comma-separated group_by with at least 2 columns"})
            
            time_col = group_cols[0]
            series_col = group_cols[1]
            select_cols = ', '.join([f'"{g}"' for g in group_cols])
            group_clause = ', '.join([f'"{g}"' for g in group_cols])
            
            sql = f"""
                SELECT {select_cols}, COUNT(*) as count, AVG("{column}") as mean, SUM("{column}") as sum
                FROM data {where_clause}
                GROUP BY {group_clause}
                ORDER BY "{time_col}", "{series_col}"
                LIMIT {limit}
            """
            result = conn.execute(sql).fetchdf()
            conn.close()
            result_json = json.dumps({
                "operation": "trend_compare",
                "column": column,
                "time_axis": time_col,
                "series": series_col,
                "results": result.to_dict(orient='records')
            }, indent=2, default=str)
            return _check_output_size(result_json, "trend_compare")
        
        elif operation == "raw":
            sql = f"SELECT * FROM data {where_clause} LIMIT {limit}"
            result = conn.execute(sql).fetchdf()
            conn.close()
            result_json = json.dumps({"operation": "raw", "rows": len(result), "data": result.to_dict(orient='records')}, indent=2, default=str)
            return _check_output_size(result_json, "raw")
        
        else:
            conn.close()
            return json.dumps({"error": f"Unknown operation '{operation}' or missing required parameters"})
    
    except Exception as e:
        return json.dumps({"error": f"DuckDB query failed: {str(e)}"})


def _query_with_pandas(df: pd.DataFrame, operation: str, column: str, filters: str, group_by: str, limit: int) -> str:
    """Fallback query execution using Pandas (for compatibility)"""
    try:
        df = df.copy()
        
        # Apply filters with range support
        if filters:
            try:
                filter_dict = json.loads(filters)
                for col, val in filter_dict.items():
                    if col not in df.columns:
                        continue
                    
                    val_str = str(val).strip()
                    applied = False
                    
                    # Check for comparison operators
                    if val_str.startswith('>='):
                        try:
                            num = float(val_str[2:].strip())
                            df = df[pd.to_numeric(df[col], errors='coerce') >= num]
                            applied = True
                        except ValueError:
                            pass
                    elif val_str.startswith('<='):
                        try:
                            num = float(val_str[2:].strip())
                            df = df[pd.to_numeric(df[col], errors='coerce') <= num]
                            applied = True
                        except ValueError:
                            pass
                    elif val_str.startswith('>') and not val_str.startswith('>='):
                        try:
                            num = float(val_str[1:].strip())
                            df = df[pd.to_numeric(df[col], errors='coerce') > num]
                            applied = True
                        except ValueError:
                            pass
                    elif val_str.startswith('<') and not val_str.startswith('<='):
                        try:
                            num = float(val_str[1:].strip())
                            df = df[pd.to_numeric(df[col], errors='coerce') < num]
                            applied = True
                        except ValueError:
                            pass
                    
                    # Check for range format
                    if not applied and '-' in val_str:
                        parts = val_str.split('-', 1)
                        if len(parts) == 2:
                            min_part, max_part = parts[0].strip(), parts[1].strip()
                            
                            if min_part and max_part:
                                try:
                                    min_val, max_val = float(min_part), float(max_part)
                                    numeric_col = pd.to_numeric(df[col], errors='coerce')
                                    df = df[(numeric_col >= min_val) & (numeric_col <= max_val)]
                                    applied = True
                                except ValueError:
                                    pass
                            elif min_part and not max_part:
                                try:
                                    min_val = float(min_part)
                                    df = df[pd.to_numeric(df[col], errors='coerce') >= min_val]
                                    applied = True
                                except ValueError:
                                    pass
                            elif not min_part and max_part:
                                try:
                                    max_val = float(max_part)
                                    df = df[pd.to_numeric(df[col], errors='coerce') <= max_val]
                                    applied = True
                                except ValueError:
                                    pass
                    
                    # Default: text contains match
                    if not applied:
                        df = df[df[col].astype(str).str.contains(str(val), case=False, na=False)]
            except json.JSONDecodeError:
                return json.dumps({"error": "Invalid filters JSON format"})
        
        # Execute operation (same logic as before)
        if operation == "count":
            if group_by and group_by in df.columns:
                result = df.groupby(group_by).size().sort_values(ascending=False).head(limit)
                return json.dumps({"operation": "count", "group_by": group_by, "results": result.to_dict()}, indent=2)
            else:
                return json.dumps({"operation": "count", "total_rows": len(df)})
        
        elif operation == "sum" and column:
            if column not in df.columns:
                return json.dumps({"error": f"Column '{column}' not found"})
            if group_by and group_by in df.columns:
                result = df.groupby(group_by)[column].sum().sort_values(ascending=False).head(limit)
                return json.dumps({"operation": "sum", "column": column, "group_by": group_by, "results": result.to_dict()}, indent=2)
            else:
                return json.dumps({"operation": "sum", "column": column, "total": float(df[column].sum())})
        
        elif operation == "mean" and column:
            if column not in df.columns:
                return json.dumps({"error": f"Column '{column}' not found"})
            if group_by and group_by in df.columns:
                result = df.groupby(group_by)[column].mean().sort_values(ascending=False).head(limit)
                return json.dumps({"operation": "mean", "column": column, "group_by": group_by, "results": result.to_dict()}, indent=2)
            else:
                return json.dumps({"operation": "mean", "column": column, "average": float(df[column].mean())})
        
        elif operation == "unique" and column:
            if column not in df.columns:
                return json.dumps({"error": f"Column '{column}' not found"})
            result = df[column].value_counts().head(limit)
            return json.dumps({"operation": "unique", "column": column, "results": result.to_dict()}, indent=2)
        
        elif operation == "trend" and column and group_by:
            if column not in df.columns or group_by not in df.columns:
                return json.dumps({"error": "Column or group_by not found"})
            result = df.groupby(group_by)[column].agg(['count', 'sum', 'mean']).head(limit)
            return json.dumps({"operation": "trend", "column": column, "group_by": group_by, "results": result.to_dict()}, indent=2)
        
        elif operation == "top" and column:
            if column not in df.columns:
                return json.dumps({"error": f"Column '{column}' not found"})
            if group_by and group_by in df.columns:
                result = df.nlargest(limit, column)[[group_by, column]]
            else:
                result = df.nlargest(limit, column)[column]
            return json.dumps({"operation": "top", "column": column, "results": result.to_dict(orient='records')}, indent=2)
        
        elif operation == "keyword_count" and column:
            if column not in df.columns:
                return json.dumps({"error": f"Column '{column}' not found"})
            # Auto-detect delimiter
            sample_text = " ".join(df[column].dropna().head(50).astype(str))
            if sample_text.count(';') >= 3:
                delim = ';'
            elif sample_text.count('|') >= 3:
                delim = '|'
            else:
                delim = ','
            keywords = df[column].dropna().str.split(delim).explode().str.strip().str.lower()
            keywords = keywords[keywords != '']
            if group_by and group_by in df.columns:
                expanded = df[[column, group_by]].dropna(subset=[column]).copy()
                expanded[column] = expanded[column].str.split(delim)
                expanded = expanded.explode(column)
                expanded[column] = expanded[column].str.strip().str.lower()
                expanded = expanded[expanded[column] != '']
                kw_totals = expanded[column].value_counts()
                top_kws = kw_totals[kw_totals >= 2].head(8).index.tolist()
                if top_kws:
                    expanded = expanded[expanded[column].isin(top_kws)]
                counts = expanded.groupby([column, group_by]).size().reset_index(name='count')
                # Fill missing group_by values with 0 for each keyword
                import itertools
                all_groups = sorted(df[group_by].dropna().unique())
                grid = pd.DataFrame(list(itertools.product(top_kws if top_kws else counts[column].unique(), all_groups)), columns=[column, group_by])
                result = grid.merge(counts, on=[column, group_by], how='left').fillna({'count': 0})
                result['count'] = result['count'].astype(int)
                result = result.sort_values([group_by, column])
                return json.dumps({"operation": "keyword_count", "column": column, "group_by": group_by, "total_keywords": len(result), "results": result.to_dict(orient='records')}, indent=2, default=str)
            else:
                result = keywords.value_counts().head(limit)
                return json.dumps({"operation": "keyword_count", "column": column, "total_keywords": len(result), "results": result.to_dict()}, indent=2, default=str)
        
        elif operation == "trend_compare" and column and group_by:
            group_cols = [g.strip() for g in group_by.split(',')]
            if len(group_cols) < 2:
                return json.dumps({"error": "trend_compare requires comma-separated group_by with at least 2 columns"})
            missing = [g for g in group_cols if g not in df.columns]
            if missing:
                return json.dumps({"error": f"Columns not found: {missing}"})
            if column not in df.columns:
                return json.dumps({"error": f"Column '{column}' not found"})
            result = df.groupby(group_cols)[column].agg(['count', 'sum', 'mean']).reset_index().head(limit)
            return json.dumps({"operation": "trend_compare", "column": column, "time_axis": group_cols[0], "series": group_cols[1], "results": result.to_dict(orient='records')}, indent=2, default=str)
        
        elif operation == "raw":
            result = df.head(limit)
            return json.dumps({"operation": "raw", "rows": len(result), "data": result.to_dict(orient='records')}, indent=2, default=str)
        
        else:
            return json.dumps({"error": f"Unknown operation '{operation}' or missing required parameters"})
    
    except Exception as e:
        return json.dumps({"error": f"Pandas query failed: {str(e)}"})


@function_tool
def query_dataset(
    file_id: str,
    operation: str,
    column: str = None,
    filters: str = None,
    group_by: str = None,
    limit: int = 10000
) -> str:
    """
    Query the dataset with flexible operations (count, sum, mean, unique, trend, raw).
    Uses DuckDB for efficient querying of large datasets.
    
    IMPORTANT: If a data scope is active, queries will be automatically
    filtered to that scope. You don't need to add these filters manually.

    TEMPORAL DEFAULT - CRITICAL:
    If you are analyzing change over time or creating any time-based view, DO NOT add a year/date/month filter
    unless the user explicitly specifies the period. The default is the FULL available timeline.
    
    Args:
        file_id: Unique identifier of the dataset
        operation: Operation to perform - 'count', 'sum', 'mean', 'unique', 'trend', 'raw', 'top', 'keyword_count', 'trend_compare'
            - keyword_count: Split delimited values in a column and count individual item frequencies. Use group_by to see keyword trends over time.
            - trend_compare: Like 'trend' but supports multi-series comparison. Use comma-separated group_by for time and series columns.
        column: Column name to operate on (required for most operations)
        filters: JSON string of filters. Supports exact matches, ranges, minimums, maximums, comparisons, and combined filters.
        group_by: Column to group results by. For trend_compare, use comma-separated time and series columns.
        limit: Maximum number of results to return (default 100)
        
    Returns:
        JSON string with query results
    """
    from utils.logger import logger
    
    try:
        if file_id not in uploaded_datasets:
            return json.dumps({"error": f"Dataset '{file_id}' not found"})
        
        dataset_info = uploaded_datasets[file_id]
        db_path = dataset_info.get("db_path")
        
        # ============ AUTO-APPLY DATA SCOPE ============
        # Merge current_data_scope with user-provided filters
        merged_filters = {}
        
        # First, apply global data scope.
        if current_data_scope:
            merged_filters.update(current_data_scope)
            logger.info(f"Auto-applying data scope: {current_data_scope}")
            logger.debug(f"Auto-applying data scope to query: {current_data_scope}")
        
        # Then, apply user-provided filters (these override scope if conflict)
        if filters:
            try:
                user_filters = json.loads(filters) if isinstance(filters, str) else filters
                merged_filters.update(user_filters)
            except json.JSONDecodeError:
                pass
        
        # Convert merged filters back to JSON string
        final_filters = json.dumps(merged_filters) if merged_filters else None
        
        if merged_filters:
            logger.info(f"Final query filters: {merged_filters}")
        
        # Use DuckDB if available, otherwise fall back to Pandas
        if db_path and os.path.exists(db_path):
            return _query_with_duckdb(db_path, operation, column, final_filters, group_by, limit)
        else:
            return _query_with_pandas(dataset_info["df"], operation, column, final_filters, group_by, limit)
    
    except Exception as e:
        return json.dumps({"error": f"Query failed: {str(e)}"})


@function_tool
def generate_vegalite_chart(
    chart_type: str,
    x_field: str,
    y_field: str,
    title: str,
    data_json: str,
    color_field: str = None,
    size_field: str = None
) -> str:
    """
    Generate Vega-Lite chart specification for visualization.
    
    Args:
        chart_type: Type of chart:
                   - arc: for pie/donut charts
                   - bar: for bar charts (vertical)
                   - horizontal-bar: for horizontal bar charts
                   - line: for line charts
                   - scatter: for scatter plots
                   - map: for geographic scatter plots (requires longitude/latitude data)
        x_field: Field name for x-axis (or longitude field for map, category for pie)
        y_field: Field name for y-axis (or latitude field for map, value for pie)
        title: Chart title
        data_json: JSON string of data values (list of objects)
        color_field: (Optional) Field name for color encoding (useful for map/scatter)
        size_field: (Optional) Field name for size encoding (useful for map/scatter)
        
    Returns:
        JSON string of Vega-Lite specification, or error message if duplicate
        
    Example for map visualization:
        generate_vegalite_chart(
            chart_type="map",
            x_field="longitude",
            y_field="latitude",
            title="Geographic Distribution",
            data_json='[{"longitude": -73.9, "latitude": 40.7, "price": 100, "name": "Apt1"}, ...]',
            color_field="price"
        )
    """
    from utils.logger import logger
    
    # ==================== DUPLICATE VISUALIZATION CHECK ====================
    # Check if a similar visualization has already been generated in this session
    # Uses data hash to detect semantically identical charts even with different field names
    if _is_duplicate_visualization(chart_type, x_field, y_field, title, data_json):
        logger.warning(f"Skipping duplicate chart: {chart_type} ({x_field} vs {y_field})")
        return json.dumps({
            "error": f"A similar {chart_type} chart with the same data has already been generated in this session. Please try a different visualization approach.",
            "suggestion": "Consider: 1) Different data grouping (by region, attack type, etc.), 2) Different time period or filter, 3) Different chart type, 4) Different metric (fatalities vs incidents)."
        })
    
    try:
        data_values = json.loads(data_json)
        
        logger.debug(f"Generating {chart_type} chart: {title}")
        logger.debug(f"Chart fields: x={x_field}, y={y_field}, rows={len(data_values)}")
        
        if len(data_values) > 0:
            sample = data_values[0]
            data_keys = list(sample.keys())
            logger.debug(f"Chart sample data: {sample}")
            logger.debug(f"Chart data keys: {data_keys}")
            
            # Validate field names match data keys.
            if x_field not in data_keys:
                logger.warning(f"x_field '{x_field}' NOT FOUND in data keys: {data_keys}")
                # Try to find similar key
                for key in data_keys:
                    if x_field.lower() in key.lower() or key.lower() in x_field.lower():
                        logger.debug(f"Replacing x_field '{x_field}' with '{key}'")
                        x_field = key
                        break
            
            if y_field not in data_keys:
                logger.warning(f"y_field '{y_field}' NOT FOUND in data keys: {data_keys}")
                # Try to find similar key
                for key in data_keys:
                    if y_field.lower() in key.lower() or key.lower() in y_field.lower():
                        logger.debug(f"Replacing y_field '{y_field}' with '{key}'")
                        y_field = key
                        break
        else:
            logger.warning("Empty chart data values")
        
        # Auto-detect keyword trend data → force stacked bar
        if len(data_values) > 0 and color_field:
            sample = data_values[0]
            color_val = str(sample.get(color_field, ""))
            has_keyword_like_color = (
                color_field.lower() in ("keyword", "keywords", "tag", "tags") or
                (len(color_val) > 3 and " " in color_val)  # multi-word like "visual analytics"
            )
            if has_keyword_like_color and chart_type in ("line", "scatter"):
                logger.debug(f"Auto-switching {chart_type} to bar for keyword trend data")
                chart_type = "bar"

        # Base specification
        spec = {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "title": title,
            "data": {"values": data_values},
            "width": 500,
            "height": 300
        }
        
        # Handle different chart types
        if chart_type == "arc":
            # Pie chart configuration
            spec["mark"] = {"type": "arc", "innerRadius": 0}
            spec["encoding"] = {
                "theta": {
                    "field": y_field,
                    "type": "quantitative"
                },
                "color": {
                    "field": x_field,
                    "type": "nominal",
                    "scale": {"scheme": "tableau20"},  # Use colorful scheme
                    "legend": {"title": x_field}
                },
                "tooltip": [
                    {"field": x_field, "type": "nominal"},
                    {"field": y_field, "type": "quantitative"}
                ]
            }
        elif chart_type == "bar" or chart_type == "horizontal-bar":
            # Bar chart - use color for categories
            spec["mark"] = "bar"
            
            # Horizontal bar chart: swap x and y
            if chart_type == "horizontal-bar":
                # Auto-detect: which field is numeric (value) vs categorical
                category_field = x_field
                value_field = y_field
                
                # Check actual data types to ensure correct mapping
                if len(data_values) > 0:
                    sample = data_values[0]
                    x_val = sample.get(x_field)
                    y_val = sample.get(y_field)
                    
                    # If x_field is numeric and y_field is string, swap them
                    x_is_numeric = isinstance(x_val, (int, float))
                    y_is_numeric = isinstance(y_val, (int, float))
                    
                    if x_is_numeric and not y_is_numeric:
                        # Agent sent fields in wrong order, swap them
                        category_field = y_field
                        value_field = x_field
                        logger.debug(f"Swapped fields for horizontal-bar: category={category_field}, value={value_field}")
                
                spec["encoding"] = {
                    "y": {
                        "field": category_field,  # Category on y-axis
                        "type": "nominal",
                        "title": category_field,
                        "sort": "-x"  # Sort by value descending
                    },
                    "x": {
                        "field": value_field,  # Value on x-axis
                        "type": "quantitative",
                        "title": value_field
                    },
                    "color": {
                        "field": category_field,
                        "type": "nominal",
                        "scale": {"scheme": "tableau10"},
                        "legend": {"title": category_field}
                    },
                    "tooltip": [
                        {"field": category_field, "type": "nominal"},
                        {"field": value_field, "type": "quantitative"}
                    ]
                }
            else:
                # Vertical bar chart (default)
                # Auto-detect field types from data
                x_is_numeric = False
                y_is_numeric = False
                if len(data_values) > 0:
                    sample = data_values[0]
                    x_val = sample.get(x_field)
                    y_val = sample.get(y_field)
                    x_is_numeric = isinstance(x_val, (int, float))
                    y_is_numeric = isinstance(y_val, (int, float))
                
                # Case 1: Both fields are categorical → use count aggregate
                if not x_is_numeric and not y_is_numeric:
                    logger.debug(f"Both fields categorical, using count aggregate: x={x_field}, color={y_field}")
                    encoding = {
                        "x": {
                            "field": x_field,
                            "type": "nominal",
                            "title": x_field
                        },
                        "y": {
                            "aggregate": "count",
                            "type": "quantitative",
                            "title": "Count"
                        },
                        "color": {
                            "field": y_field,
                            "type": "nominal",
                            "scale": {"scheme": "tableau10"},
                            "legend": {"title": y_field}
                        },
                        "tooltip": [
                            {"field": x_field, "type": "nominal"},
                            {"field": y_field, "type": "nominal"},
                            {"aggregate": "count", "type": "quantitative", "title": "Count"}
                        ]
                    }
                # Case 2: x is numeric, y is categorical → swap them
                elif x_is_numeric and not y_is_numeric:
                    logger.debug(f"Swapping fields for vertical bar: category={y_field}, value={x_field}")
                    encoding = {
                        "x": {
                            "field": y_field,
                            "type": "nominal",
                            "title": y_field
                        },
                        "y": {
                            "field": x_field,
                            "type": "quantitative",
                            "title": x_field
                        },
                        "color": {
                            "field": y_field,
                            "type": "nominal",
                            "scale": {"scheme": "tableau10"},
                            "legend": {"title": y_field}
                        },
                        "tooltip": [
                            {"field": y_field, "type": "nominal"},
                            {"field": x_field, "type": "quantitative"}
                        ]
                    }
                # Case 3: Normal — x is categorical, y is numeric
                else:
                    # Use ordinal for year-like numeric x fields so bars are ordered correctly
                    x_type = "ordinal" if x_is_numeric else "nominal"
                    encoding = {
                        "x": {
                            "field": x_field,
                            "type": x_type,
                            "title": x_field
                        },
                        "y": {
                            "field": y_field,
                            "type": "quantitative",
                            "title": y_field
                        },
                        "tooltip": [
                            {"field": x_field, "type": x_type},
                            {"field": y_field, "type": "quantitative"}
                        ]
                    }
                
                # ==================== WIDE FORMAT DETECTION for BAR ====================
                if color_field and len(data_values) > 0:
                    sample = data_values[0]
                    has_y = y_field in sample
                    has_color = color_field in sample
                    if not has_y and not has_color:
                        fold_cols = [k for k in sample.keys() if k != x_field]
                        if len(fold_cols) >= 2:
                            spec["transform"] = [
                                {"fold": fold_cols, "as": [color_field, y_field]}
                            ]
                            # Update encoding y to use folded field
                            encoding["y"] = {
                                "field": y_field,
                                "type": "quantitative",
                                "title": y_field
                            }
                            logger.debug(f"Wide format bar chart; folding {fold_cols} into ({color_field}, {y_field})")
                
                # If color_field is provided, create grouped or stacked bar chart
                if color_field and len(data_values) > 0:
                    unique_colors = len(set(str(d.get(color_field, "")) for d in data_values))
                    encoding["color"] = {
                        "field": color_field,
                        "type": "nominal",
                        "scale": {"scheme": "tableau10"},
                        "legend": {"title": color_field}
                    }
                    # Stacked bar for many categories; grouped for fewer.
                    if unique_colors < 5:
                        encoding["xOffset"] = {
                            "field": color_field,
                            "type": "nominal"
                        }
                    encoding["tooltip"].append({"field": color_field, "type": "nominal"})
                    logger.debug(f"Grouped bar chart with color_field: {color_field}")
                elif "color" not in encoding:
                    # Default: single series, color by x-axis category
                    encoding["color"] = {
                        "field": x_field,
                        "type": "nominal",
                        "scale": {"scheme": "tableau10"},
                        "legend": {"title": x_field}
                    }
                
                spec["encoding"] = encoding
        elif chart_type == "line":
            # Line chart - supports multi-series via color_field for comparisons
            spec["mark"] = {"type": "line", "point": True, "strokeWidth": 2}
            
            # ==================== WIDE FORMAT DETECTION & FOLD ====================
            # If data is in wide format but encoding expects long format,
            # automatically apply Vega-Lite fold transform to reshape the data.
            needs_fold = False
            fold_columns = []
            if color_field and len(data_values) > 0:
                sample = data_values[0]
                has_y = y_field in sample
                has_color = color_field in sample
                if not has_y and not has_color:
                    # y_field and color_field don't exist in data → likely wide format
                    # The series columns are all fields except x_field
                    fold_columns = [k for k in sample.keys() if k != x_field]
                    if len(fold_columns) >= 2:
                        needs_fold = True
                        logger.debug(f"Wide format detected; folding columns {fold_columns} into ({color_field}, {y_field})")
            
            if needs_fold:
                spec["transform"] = [
                    {"fold": fold_columns, "as": [color_field, y_field]}
                ]
            
            encoding = {
                "x": {
                    "field": x_field,
                    "type": "quantitative",  # Treat as continuous/temporal
                    "title": x_field
                },
                "y": {
                    "field": y_field,
                    "type": "quantitative",
                    "title": y_field
                },
                "tooltip": [
                    {"field": x_field, "type": "quantitative"},
                    {"field": y_field, "type": "quantitative"}
                ]
            }
            
            # Add color encoding for multi-series comparison.
            if color_field and len(data_values) > 0:
                encoding["color"] = {
                    "field": color_field,
                    "type": "nominal",
                    "scale": {"scheme": "category10"},
                    "legend": {"title": color_field}
                }
                encoding["tooltip"].append({"field": color_field, "type": "nominal"})
                logger.debug(f"Multi-series line chart with color_field: {color_field}")
            
            spec["encoding"] = encoding
        elif chart_type == "map":
            # Geographic scatter plot using longitude/latitude
            # Uses circle marks on a coordinate system
            spec["width"] = 600
            spec["height"] = 400
            spec["mark"] = {
                "type": "circle",
                "opacity": 0.7,
                "stroke": "#333",
                "strokeWidth": 0.5
            }
            
            # Build encoding
            encoding = {
                "longitude": {
                    "field": x_field,
                    "type": "quantitative"
                },
                "latitude": {
                    "field": y_field,
                    "type": "quantitative"
                }
            }
            
            # Add color encoding if specified
            if color_field and len(data_values) > 0:
                sample_val = data_values[0].get(color_field)
                if isinstance(sample_val, (int, float)):
                    encoding["color"] = {
                        "field": color_field,
                        "type": "quantitative",
                        "scale": {"scheme": "viridis"},
                        "legend": {"title": color_field}
                    }
                else:
                    encoding["color"] = {
                        "field": color_field,
                        "type": "nominal",
                        "scale": {"scheme": "category10"},
                        "legend": {"title": color_field}
                    }
            else:
                # Default color
                encoding["color"] = {"value": "#4682b4"}
            
            # Add size encoding if specified
            if size_field and len(data_values) > 0:
                encoding["size"] = {
                    "field": size_field,
                    "type": "quantitative",
                    "scale": {"range": [20, 200]},
                    "legend": {"title": size_field}
                }
            else:
                encoding["size"] = {"value": 60}
            
            # Build tooltip with all available info
            tooltip_fields = [
                {"field": x_field, "type": "quantitative", "title": "Longitude"},
                {"field": y_field, "type": "quantitative", "title": "Latitude"}
            ]
            if color_field:
                tooltip_fields.append({"field": color_field, "type": "quantitative" if isinstance(data_values[0].get(color_field), (int, float)) else "nominal"})
            if size_field:
                tooltip_fields.append({"field": size_field, "type": "quantitative"})
            
            # Add name field if exists
            if len(data_values) > 0:
                sample_keys = list(data_values[0].keys())
                for name_candidate in ["name", "title", "label", "id", "listing_name", "hotel_name"]:
                    if name_candidate in sample_keys:
                        tooltip_fields.insert(0, {"field": name_candidate, "type": "nominal", "title": "Name"})
                        break
            
            encoding["tooltip"] = tooltip_fields
            spec["encoding"] = encoding
            
            logger.debug(f"Generated map visualization with {len(data_values)} points")
        else:
            # Other charts (scatter, area, point)
            mark_type = "point" if chart_type == "scatter" else chart_type
            
            # Detect field types from data
            def detect_field_type(field, values):
                """Detect Vega-Lite field type from data values"""
                sample = [v.get(field) for v in values[:20] if v.get(field) is not None]
                if not sample:
                    return "nominal"
                if all(isinstance(v, (int, float)) for v in sample):
                    return "quantitative"
                # Check if it looks like a date
                if all(isinstance(v, str) and len(v) >= 4 for v in sample):
                    try:
                        int(str(sample[0])[:4])
                        if len(str(sample[0])) >= 8:
                            return "temporal"
                    except (ValueError, TypeError):
                        pass
                return "nominal"
            
            x_type = detect_field_type(x_field, data_values)
            y_type = detect_field_type(y_field, data_values)
            
            encoding = {
                "x": {
                    "field": x_field,
                    "type": x_type,
                    "title": x_field
                },
                "y": {
                    "field": y_field,
                    "type": y_type,
                    "title": y_field
                },
                "tooltip": [
                    {"field": x_field, "type": x_type},
                    {"field": y_field, "type": y_type}
                ]
            }
            
            # Add color encoding for scatter/point charts
            if color_field and chart_type in ("scatter", "point"):
                color_type = detect_field_type(color_field, data_values)
                encoding["color"] = {
                    "field": color_field,
                    "type": color_type,
                    "title": color_field
                }
                encoding["tooltip"].append({"field": color_field, "type": color_type})
            
            # Add size encoding for scatter/point charts
            if size_field and chart_type in ("scatter", "point"):
                encoding["size"] = {
                    "field": size_field,
                    "type": "quantitative",
                    "title": size_field
                }
                encoding["tooltip"].append({"field": size_field, "type": "quantitative"})
            
            # For scatter/point, make marks filled and slightly transparent for overlap
            if chart_type in ("scatter", "point"):
                spec["mark"] = {
                    "type": "point",
                    "filled": True,
                    "opacity": 0.7,
                    "size": 60 if not size_field else {"value": 60}
                }
                # If size_field is used, don't set fixed size in mark
                if size_field:
                    spec["mark"] = {"type": "point", "filled": True, "opacity": 0.7}
            else:
                spec["mark"] = mark_type
            
            spec["encoding"] = encoding
        
        # ==================== REGISTER SUCCESSFUL VISUALIZATION ====================
        # Register this visualization to prevent duplicates in this session
        _register_visualization(chart_type, x_field, y_field, title, data_json)
        
        return json.dumps(spec)
    except Exception as e:
        return json.dumps({"error": f"Failed to generate chart: {str(e)}"})


# ==================== Dynamic Agent Names Configuration ====================
# Stores customizable agent names (persisted in memory, reset on server restart)
# Max 2 agents per role (1 default + 1 custom)

MAX_AGENTS_PER_ROLE = 2

# Default agent configurations (cannot be deleted)
DEFAULT_AGENTS = {
    "statistics": {"name": "Statistical Analyst", "icon": "📊"},
    "visualization": {"name": "Visualization Expert", "icon": "📈"},
    "insight": {"name": "Intelligence", "icon": "💡"},
    "summary": {"name": "Summary Agent", "icon": "📝"},
    "scanner": {"name": "Data Scout", "icon": "🔍"},
}

# Active agents registry - id -> {role, name, icon, is_default}
_agent_counter = 0
AGENT_REGISTRY = {}

def _init_default_agents():
    """Initialize default agents on startup"""
    global _agent_counter, AGENT_REGISTRY
    for role, config in DEFAULT_AGENTS.items():
        _agent_counter += 1
        agent_id = f"agent_{_agent_counter}"
        AGENT_REGISTRY[agent_id] = {
            "id": agent_id,
            "role": role,
            "name": config["name"],
            "icon": config["icon"],
            "is_default": True,
        }

# Initialize on module load
_init_default_agents()


def _strip_vega_blobs(text: str) -> str:
    """
    Remove raw Vega-Lite JSON objects (including markdown-fenced ones)
    from agent text responses.
    """
    import re

    # 1) Remove markdown-fenced code blocks that contain a Vega schema reference.
    #    Match ```<optional lang> ... ``` where the inner content mentions $schema + vega.
    def _remove_fenced_vega(m: re.Match) -> str:
        inner = m.group(0)
        if '$schema' in inner and 'vega' in inner.lower():
            return ''
        return inner

    text = re.sub(r'```[a-zA-Z_-]*\s*[\s\S]*?```', _remove_fenced_vega, text)

    # 2) Remove bare (unfenced) Vega JSON blobs by brace-depth matching
    result_parts: list = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            j = i
            while j < len(text):
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            blob = text[i:j + 1]
            if '"$schema"' in blob and 'vega' in blob.lower():
                i = j + 1
                while i < len(text) and text[i] in ' \t\n\r':
                    i += 1
                continue
            else:
                result_parts.append(text[i])
                i += 1
        else:
            result_parts.append(text[i])
            i += 1
    text = "".join(result_parts).strip()

    # 3) Remove leftover empty fenced code blocks
    text = re.sub(r'```\s*```', '', text)

    return text.strip()


def _strip_backticks(text: str) -> str:
    """
    Remove single backticks wrapping short inline tokens (column names, values).
    Preserves triple-backtick code blocks.
    """
    import re
    return re.sub(r'(?<!`)` *([^`\n]{1,60}) *`(?!`)', r'\1', text)


def get_agent_display_name(role: str, client_id: str = None) -> str:
    """
    Get the display name for an agent role (client-aware).
    
    Args:
        role: Agent role (statistics, visualization, insight, scanner)
        client_id: Client ID for session-specific agent names
        
    Returns:
        Agent display name from ClientStore if available, otherwise from global registry
    """
    # Try client-specific store first
    if client_id:
        try:
            from services.client_store import get_client_store
            store = get_client_store(client_id)
            return store.get_agent_display_name(role)
        except Exception:
            pass  # Fallback to global registry
    
    # Fallback: Global registry (for backwards compatibility)
    for agent in AGENT_REGISTRY.values():
        if agent["role"] == role:
            return agent["name"]
    return role.title()

def set_agent_name(role: str, name: str) -> bool:
    """Set name for an agent role (updates first agent of that role)"""
    for agent in AGENT_REGISTRY.values():
        if agent["role"] == role:
            agent["name"] = name
            return True
    return False

def update_agent_name_by_id(agent_id: str, name: str) -> bool:
    """Update agent name by ID"""
    if agent_id in AGENT_REGISTRY:
        AGENT_REGISTRY[agent_id]["name"] = name
        return True
    return False

def get_all_agent_configs() -> list:
    """Get all agent configurations"""
    return list(AGENT_REGISTRY.values())

def get_agents_by_role(role: str, client_id: str = None) -> list:
    """
    Get all agents for a specific role (client-aware).
    
    Args:
        role: Agent role (statistics, visualization, insight, scanner)
        client_id: Client ID for session-specific agent list
        
    Returns:
        List of agents from ClientStore if available, otherwise from global registry
    """
    # Try client-specific store first
    if client_id:
        try:
            from services.client_store import get_client_store
            store = get_client_store(client_id)
            return store.get_agents_by_role(role)
        except Exception:
            pass  # Fallback to global registry
    
    # Fallback: Global registry
    return [a for a in AGENT_REGISTRY.values() if a["role"] == role]

def can_add_agent(role: str) -> bool:
    """Check if we can add another agent to this role"""
    count = len(get_agents_by_role(role))
    return count < MAX_AGENTS_PER_ROLE

def create_agent(role: str, name: str) -> dict:
    """Create a new custom agent for a role"""
    global _agent_counter
    
    if role not in DEFAULT_AGENTS:
        raise ValueError(f"Invalid role: {role}")
    
    if not can_add_agent(role):
        raise ValueError(f"Max {MAX_AGENTS_PER_ROLE} agents per role")
    
    _agent_counter += 1
    agent_id = f"agent_{_agent_counter}"
    
    agent = {
        "id": agent_id,
        "role": role,
        "name": name,
        "icon": DEFAULT_AGENTS[role]["icon"],
        "is_default": False,
    }
    AGENT_REGISTRY[agent_id] = agent
    return agent

def delete_agent(agent_id: str) -> bool:
    """Delete a custom agent (cannot delete defaults)"""
    if agent_id not in AGENT_REGISTRY:
        return False
    
    agent = AGENT_REGISTRY[agent_id]
    if agent["is_default"]:
        raise ValueError("Cannot delete default agent")
    
    del AGENT_REGISTRY[agent_id]
    return True

def get_available_roles() -> list:
    """Get roles that can have more agents added"""
    return [
        {
            "role": role,
            "name": config["name"],
            "icon": config["icon"],
            "can_add": can_add_agent(role),
            "current_count": len(get_agents_by_role(role)),
            "max_count": MAX_AGENTS_PER_ROLE,
        }
        for role, config in DEFAULT_AGENTS.items()
    ]


# ==================== Agent Counterpoint System ====================
# Enables same-role agents to provide alternative perspectives on significant claims

async def evaluate_counterpoint_need(analysis: str) -> dict:
    """
    Evaluate if an analysis contains a significant claim worth a counterpoint.
    
    Returns:
        dict with keys: score (1-10), reason, counterpoint_angle
    """
    from utils.logger import logger
    
    eval_prompt = build_counterpoint_eval_prompt(analysis)
    
    try:
        eval_agent = Agent(
            name="Counterpoint Evaluator",
            instructions=COUNTERPOINT_EVALUATOR_INSTRUCTIONS,
            model=settings.get_utility_model(),
            model_settings=UTILITY_JSON_MODEL_SETTINGS,
            tools=[],
        )
        
        result = await Runner.run(
            starting_agent=eval_agent,
            input=eval_prompt,
            max_turns=3
        )
        
        response_text = (result.final_output or "").strip()
        
        # Parse JSON
        import json
        import re
        json_match = re.search(r'\{[^}]+\}', response_text)
        if json_match:
            data = json.loads(json_match.group())
            score = int(data.get("score", 0))
            logger.info(f"Counterpoint eval: {score}/10 - {data.get('reason', '')[:50]}")
            return {
                "score": score,
                "reason": data.get("reason", ""),
                "counterpoint_angle": data.get("counterpoint_angle", "")
            }
        
        return {"score": 0, "reason": "Could not parse", "counterpoint_angle": ""}
        
    except Exception as e:
        logger.error(f"Counterpoint evaluation failed: {str(e)}")
        return {"score": 0, "reason": str(e), "counterpoint_angle": ""}


async def generate_counterpoint(
    agent_config: dict,
    original_analysis: str,
    counterpoint_angle: str,
    file_id: str
) -> Optional[dict]:
    """
    Generate a constructive counterpoint from the second agent.
    
    Args:
        agent_config: The second agent's config (id, name, role, etc.)
        original_analysis: The original agent's analysis
        counterpoint_angle: Suggested angle to explore
        file_id: Dataset file ID
    
    Returns:
        dict with counterpoint content, or None
    """
    from utils.logger import logger
    
    counterpoint_prompt = build_counterpoint_prompt(
        agent_config["name"],
        original_analysis,
        counterpoint_angle,
        file_id,
    )
    
    try:
        # Get the appropriate agent based on role
        role = agent_config.get("role", "")
        agent = DISCUSSION_AGENTS.get(role)
        
        if not agent:
            logger.warning(f"No agent found for role: {role}")
            return None
        
        # Set up dataset context if needed
        if file_id in uploaded_datasets:
            dataset_info = uploaded_datasets[file_id]
            if "duckdb_path" in dataset_info:
                global active_sessions
                session_key = f"{file_id}_counterpoint"
                if session_key not in active_sessions:
                    active_sessions[session_key] = SQLiteSession(database=":memory:")
        
        result = await Runner.run(
            starting_agent=agent,
            input=counterpoint_prompt,
            max_turns=5
        )
        
        response_text = (result.final_output or "").strip()
        
        if not response_text or len(response_text) < 20:
            logger.warning("Counterpoint response too short")
            return None
        
        logger.info(f"Counterpoint from {agent_config['name']}: {response_text[:80]}...")
        
        return {
            "content": response_text,
            "agent_name": agent_config["name"],
            "agent_role": role,
            "is_counterpoint": True
        }
        
    except Exception as e:
        logger.error(f"Counterpoint generation failed: {str(e)}")
        return None


async def check_and_trigger_counterpoint(
    agent_role: str,
    original_response: str,
    file_id: str,
    client_id: Optional[str] = None  # Client ID for session isolation
) -> Optional[dict]:
    """
    Check if counterpoint is warranted and generate if so.
    Only triggers if:
    1. There are 2 agents for this role
    2. The claim is significant (score >= 7)
    
    Args:
        agent_role: Role of the agent that made the original response
        original_response: The original analysis text
        file_id: Dataset file ID
        client_id: Client ID for session isolation
    
    Returns:
        Counterpoint dict if generated, None otherwise
    """
    from utils.logger import logger
    
    # 1. Check if we have 2 agents for this role (client-aware)
    agents_for_role = get_agents_by_role(agent_role, client_id)
    if len(agents_for_role) < 2:
        logger.debug(f"No counterpoint - only {len(agents_for_role)} agent(s) for role '{agent_role}'")
        return None
    
    # 2. Evaluate if claim is significant
    logger.info(f"Evaluating counterpoint need for {agent_role}...")
    eval_result = await evaluate_counterpoint_need(original_response)
    
    if eval_result["score"] < 7:
        logger.info(f"Counterpoint skipped - score {eval_result['score']}/10 (need 7+)")
        return None
    
    # 3. Get the second agent (non-default, or second in list)
    # Prefer non-default agent for counterpoint
    second_agent = None
    for agent in agents_for_role:
        if not agent.get("is_default", True):
            second_agent = agent
            break
    
    # Fallback to second agent in list
    if not second_agent and len(agents_for_role) >= 2:
        second_agent = agents_for_role[1]
    
    if not second_agent:
        return None
    
    logger.info(f"Counterpoint triggered! {second_agent['name']} will provide alternative perspective")
    
    # 4. Generate counterpoint
    return await generate_counterpoint(
        second_agent,
        original_response,
        eval_result["counterpoint_angle"],
        file_id
    )


# ==================== Agent Definitions ====================

# Statistical Analyst Agent
analyst_agent = Agent(
    name="Statistical Analyst Agent",
    instructions=AGENT_INSTRUCTIONS["statistics"],
    model=settings.get_agent_model('statistics'),
    model_settings=CORE_AGENT_MODEL_SETTINGS,
    tools=[get_dataset_info, query_dataset, get_column_summary],
)

# Visualization Expert Agent
visualizer_agent = Agent(
    name="Visualization Expert Agent",
    instructions=AGENT_INSTRUCTIONS["visualization"],
    model=settings.get_agent_model('visualization'),
    model_settings=CORE_AGENT_MODEL_SETTINGS,
    tools=[get_dataset_info, query_dataset, generate_vegalite_chart],
)

# Intelligence Agent
insight_agent = Agent(
    name="Intelligence Agent",
    instructions=AGENT_INSTRUCTIONS["insight"],
    model=settings.get_agent_model('insight'),
    model_settings=CORE_AGENT_MODEL_SETTINGS,
    tools=[get_dataset_info, query_dataset, get_column_summary, WebSearchTool()],
)

# Proactive Scanner Agent - Discovers patterns and proposes questions
proactive_scanner_agent = Agent(
    name="Data Scout Agent",
    instructions=AGENT_INSTRUCTIONS["scanner"],
    model=settings.get_agent_model('scanner'),
    model_settings=CORE_AGENT_MODEL_SETTINGS,
    tools=[get_dataset_info, query_dataset, get_column_summary],
)

# ==================== Summary Tools ====================

@function_tool
def get_conversation_data(file_id: str) -> str:
    """
    Get all posts and replies related to a file for summarization.
    Returns only essential information to minimize context usage.
    
    Args:
        file_id: The file ID to get conversation data for
        
    Returns:
        JSON string containing summarized conversation data
    """
    from services.client_store import client_store_manager
    
    try:
        # Get client_id from context variable
        client_id = current_client_id.get()
        if not client_id:
            return json.dumps({"error": "No client context available", "posts": []})
        
        # Get posts from ClientStore
        store = client_store_manager.get_store(client_id)
        
        # Filter posts related to this file (with null safety)
        related_posts = [
            post for post in store.posts_db 
            if post and post.get('file_metadata', {}).get('file_id') == file_id
        ]
        
        conversation_data = {
            "total_posts": len(related_posts),
            "total_replies": sum(len(post.get('replies', [])) for post in related_posts if post),
            "posts": []
        }
        
        MAX_POSTS = 20
        selected_posts = related_posts[-MAX_POSTS:]
        
        for post in selected_posts:
            content = truncate_text(
                post.get('content', ''),
                CONVERSATION_MESSAGE_TOKEN_BUDGET,
                preserve="head"
            )
            
            post_data = {
                "id": post['id'],
                "author": post['author'],
                "author_type": post['author_type'],
                "content": content,
                "has_visualization": post.get('visualization') is not None,
                "reply_count": len(post.get('replies', [])),
                "key_replies": []
            }
            
            replies = post.get('replies', [])
            if len(replies) > 0:
                first_reply = replies[0]
                first_reply_content = truncate_text(
                    first_reply.get('content', ''),
                    CONVERSATION_MESSAGE_TOKEN_BUDGET,
                    preserve="head"
                )
                
                post_data['key_replies'].append({
                    "author": first_reply['author'],
                    "author_type": first_reply['author_type'],
                    "content": first_reply_content,
                    "has_visualization": first_reply.get('visualization') is not None
                })
                
                if len(replies) > 1:
                    last_reply = replies[-1]
                    last_reply_content = truncate_text(
                        last_reply.get('content', ''),
                        CONVERSATION_MESSAGE_TOKEN_BUDGET,
                        preserve="head"
                    )
                    
                    post_data['key_replies'].append({
                        "author": last_reply['author'],
                        "author_type": last_reply['author_type'],
                        "content": last_reply_content,
                        "has_visualization": last_reply.get('visualization') is not None
                    })
            
            conversation_data['posts'].append(post_data)
        
        if len(related_posts) > MAX_POSTS:
            conversation_data['note'] = f"Showing {MAX_POSTS} of {len(related_posts)} posts (most recent)"

        before_trim_count = len(conversation_data["posts"])
        conversation_data = trim_json_list_payload(
            conversation_data,
            "posts",
            CONVERSATION_DATA_TOKEN_BUDGET
        )
        after_trim_count = len(conversation_data["posts"])
        if after_trim_count < before_trim_count:
            conversation_data["note"] = f"Showing {after_trim_count} of {len(related_posts)} posts within the context budget"
        
        return json.dumps(conversation_data, indent=2, ensure_ascii=False)
        
    except Exception as e:
        return json.dumps({"error": f"Failed to get conversation data: {str(e)}"})


# Summary Agent (Feed) - Synthesizes findings from other agents into narratives.
# Separate from the Analysis Hub summary writer; this one participates in live discussions.
summary_feed_agent = Agent(
    name="Summary Agent",
    instructions=AGENT_INSTRUCTIONS["summary"],
    model=settings.get_agent_model('summary'),
    model_settings=CORE_AGENT_MODEL_SETTINGS,
    tools=[get_dataset_info, get_conversation_data],
)

# Internal request router for legacy single-response endpoints.
request_router = Agent(
    name="Request Router",
    instructions=REQUEST_ROUTER_INSTRUCTIONS,
    model=settings.get_utility_model(),
    model_settings=UTILITY_JSON_MODEL_SETTINGS,
    handoffs=[analyst_agent, visualizer_agent, insight_agent],
)

# Internal writer used by the Analysis Hub summary view.
summary_writer = Agent(
    name="Summary Writer",
    instructions=SUMMARY_WRITER_INSTRUCTIONS,
    model=settings.get_agent_model('insight'),
    model_settings=SUMMARY_MODEL_SETTINGS,
    tools=[get_conversation_data],
)

# Internal generator for follow-up question cards.
next_step_generator = Agent(
    name="Next-Step Generator",
    instructions=NEXT_STEP_GENERATOR_INSTRUCTIONS,
    model=settings.get_agent_model('insight'),
    model_settings=NEXT_STEP_MODEL_SETTINGS,
    tools=[get_conversation_data],
)


# ==================== Runner Functions ====================

async def run_agent_analysis(
    message: str,
    file_path: Optional[str] = None,
    file_id: Optional[str] = None,
    agent: Optional[Agent] = None,
    post_id: Optional[int] = None,  # POST 기반 session을 위한 post_id
    data_scope: Optional[Dict[str, Any]] = None,
    ws_context: Optional[str] = None,
    image_file_path: Optional[str] = None,
    client_id: Optional[str] = None,
    display_name: Optional[str] = None,
    agent_role: Optional[str] = None,
    reference_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run agent analysis using OpenAI Agents SDK with dataset query tools
    
    Args:
        message: User message
        file_path: Path to file (if any)
        file_id: File ID for dataset storage
        agent: Specific core agent to use. Defaults to the internal request router.
        post_id: POST ID for session management
        data_scope: Data scope filters
        ws_context: WebSocket context - "post" for new posts, "reply" for replies (default: "reply")
        image_file_path: Path to attached image file for Vision API processing
        client_id: Client ID for session isolation (required for multi-tab support)
        
    Returns:
        Dict with agent response and metadata
    """
    from utils.logger import logger
    from services.websocket_manager import ws_manager
    global current_data_scope, pending_image_data  # Module-level globals
    
    # Determine WebSocket context (default to "reply" unless explicitly set to "post")
    emit_context = ws_context if ws_context else "reply"
    
    # ============ SET DATA SCOPE ============
    # Set the current data scope for this analysis run
    # This will be automatically applied to all query_dataset calls
    if data_scope:
        current_data_scope = data_scope
        logger.info(f"Data scope set: {data_scope}")
    else:
        current_data_scope = {}
    
    # ============ PARSE POST REFERENCES (#N) ============
    # Check for #N references to other posts and build reference context
    # Use provided reference_context (from @mention) or start fresh
    base_context = reference_context or ""
    if base_context:
        logger.info(f"Received reference_context from @mention: {len(base_context)} chars")
    
    if client_id:
        ref_post_ids, cleaned_message = parse_post_references(message)
        if ref_post_ids:
            post_ref_context = build_reference_context(ref_post_ids, client_id)
            if post_ref_context:
                logger.info(f"Injecting reference context from posts: {ref_post_ids}")
                # Combine base_context with post references
                base_context = f"{base_context}\n\n{post_ref_context}" if base_context else post_ref_context
                # Update message with references removed (context will be added separately)
                message = cleaned_message
    
    reference_context = truncate_text(
        base_context,
        AGENT_REFERENCE_TOKEN_BUDGET,
        preserve="middle"
    ) if base_context else ""
    
    # Use the internal router by default for legacy single-response requests.
    is_router = False
    if agent is None:
        agent = request_router
        is_router = True
    
    logger.info(f"Running agent: {agent.name}")
    
    # ========== WEBSOCKET: Emit typing start ==========
    # For router runs, emit as "Data Analysis Team" because the final specialist
    # is not known until handoff completes.
    # For core specialists, use custom display_name if provided, otherwise agent.name.
    if is_router:
        start_agent_name = "Data Analysis Team"
    else:
        start_agent_name = display_name if display_name else agent.name
    await ws_manager.emit_agent_typing(start_agent_name, post_id, "start", context=emit_context, client_id=client_id, role=agent_role)
    
    user_request_for_agent = message
    if reference_context:
        user_request_for_agent = f"[Current User Request]\n{message}\n\n[Context from this thread or referenced posts]\n{reference_context}\n\nIMPORTANT: The user's request above is about the CURRENT post's topic. If referenced posts (#N) are included, the user wants you to USE that information in relation to the current discussion — compare, contrast, verify, or extend. Always address the current post's topic first, then relate it to the referenced content."
    
    agent_input = user_request_for_agent
    
    # Build scope info string for agent prompt
    scope_info = ""
    if data_scope:
        scope_str = ", ".join([f"{k}={v}" for k, v in data_scope.items()])
        scope_info = f"""
ACTIVE DATA SCOPE: {scope_str}
All your queries will be automatically filtered to this scope.
You don't need to add these filters manually - they're applied automatically.
Focus your analysis on this specific subset of the data.
"""

    temporal_default_info = """
TEMPORAL DEFAULT RULE:
If you perform any time-based analysis or chart (trend, change over time, timeline, trajectory, year-by-year/month-by-month view),
use the FULL available timeline by default.
- Only restrict the time period if the user explicitly specifies one.
- If the user asks about a country/category over time, filter the country/category but keep the full timeline.
- Before calling query_dataset() for time-based analysis, double-check that your filters do NOT include a year/date/month unless the user explicitly requested it.
"""
    dataset_runtime_context = ""
    
    # ============ PROCESS ATTACHED IMAGE (Priority over file_path) ============
    # If an image is explicitly attached, prepare it for Vision API
    if image_file_path:
        try:
            import base64
            logger.info(f"Processing attached image for Vision API: {image_file_path}")
            
            with open(image_file_path, 'rb') as img_file:
                img_base64 = base64.b64encode(img_file.read()).decode('utf-8')
            
            # Determine MIME type
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
            file_ext = image_file_path.lower()[image_file_path.rfind('.'):]
            mime_map = {
                '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'
            }
            mime_type = mime_map.get(file_ext, 'image/png')
            image_data_url = f"data:{mime_type};base64,{img_base64}"
            
            # Store image for multimodal input (Vision API)
            # Note: pending_image_data is a module-level global, declared earlier
            pending_image_data = image_data_url
            
            logger.info(f"Attached image prepared for Vision API ({len(img_base64)} chars base64)")
        except Exception as e:
            logger.error(f"Failed to read attached image: {str(e)}")
    
    # Load CSV dataset into memory for querying (if CSV file provided)
    if file_id and file_id in uploaded_datasets:
        # Dataset already loaded - just add file_id to prompt
        dataset_info = uploaded_datasets[file_id]
        dataset_runtime_context = f"""[Dataset loaded successfully with ID: {file_id}]
- Rows: {dataset_info['rows']:,}
- Columns: {len(dataset_info['columns'])}
- Available tools: get_dataset_info(), query_dataset(), get_column_summary()
{scope_info}
{temporal_default_info}
IMPORTANT: Always use this exact file_id when calling tools: {file_id}
"""
        agent_input = f"""{user_request_for_agent}

{dataset_runtime_context}"""
        logger.info(f"Using existing dataset in memory")
    elif file_path and file_id:
        if file_path.endswith('.csv'):
            logger.info(f"CSV file detected - loading into memory for agent queries")
            success = load_dataset(file_path, file_id)
            
            if success:
                dataset_info = uploaded_datasets[file_id]
                dataset_runtime_context = f"""[Dataset loaded successfully with ID: {file_id}]
- Rows: {dataset_info['rows']:,}
- Columns: {len(dataset_info['columns'])}
- Available tools: get_dataset_info(), query_dataset(), get_column_summary()
{scope_info}
{temporal_default_info}
IMPORTANT: Always use this exact file_id when calling tools: {file_id}
"""
                agent_input = f"""{user_request_for_agent}

{dataset_runtime_context}"""
                logger.info(f"Dataset ready for agent queries")
            else:
                agent_input = f"{user_request_for_agent}\n\n[Error: Failed to load dataset]"
        else:
            # Check if it's an image file
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
            file_ext = file_path.lower()[file_path.rfind('.'):]
            
            if file_ext in image_extensions:
                # Handle image file with Vision API format
                try:
                    import base64
                    logger.info(f"Reading image file for Vision API: {file_path}")
                    
                    with open(file_path, 'rb') as img_file:
                        img_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                    
                    # Determine MIME type
                    mime_map = {
                        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                        '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'
                    }
                    mime_type = mime_map.get(file_ext, 'image/png')
                    image_data_url = f"data:{mime_type};base64,{img_base64}"
                    
                    # Store image for multimodal input (Vision API)
                    # global already declared at function start
                    pending_image_data = image_data_url
                    
                    # Just use the original message - image will be added to input list later
                    agent_input = user_request_for_agent
                    
                    logger.info(f"Image prepared for Vision API ({len(img_base64)} chars base64)")
                except Exception as e:
                    logger.error(f"Failed to read image: {str(e)}")
                    agent_input = f"{user_request_for_agent}\n\n[Error: Could not read image file]"
            else:
                # For non-CSV, non-image files (like TXT), read content directly
                try:
                    logger.info(f"Reading text file: {file_path}")
                    with open(file_path, 'r', encoding='utf-8') as f:
                        file_content = f.read()
                    
                    original_file_tokens = estimate_tokens(file_content)
                    file_content = truncate_text(
                        file_content,
                        TEXT_FILE_TOKEN_BUDGET,
                        preserve="head"
                    )
                    if original_file_tokens > TEXT_FILE_TOKEN_BUDGET:
                        file_content = f"{file_content}\n\n[File content omitted beyond token budget: approximately {original_file_tokens} tokens total]"
                    
                    agent_input = f"{user_request_for_agent}\n\n---FILE CONTENT---\n{file_content}\n---END FILE---"
                    logger.info(f"Text file content included ({len(file_content)} chars)")
                except Exception as e:
                    logger.error(f"Failed to read file: {str(e)}")
                    agent_input = f"{user_request_for_agent}\n\n[Error: Could not read file]"
    
    # Get or create session for this conversation
    # Session ID now includes client_id for complete isolation
    # Use the same session key as trigger_agent_discussion for consistency.
    session = None
    global active_sessions
    
    # Build client prefix for file-based session fallback
    client_prefix = f"c_{client_id[:12]}_" if client_id else ""
    
    if post_id is not None and client_id:
        # POST-based session - SAME format as Discussion for shared context
        session_key = f"post_{post_id}_discussion_{client_id}_{SERVER_START_TIME}"
        
        if session_key not in active_sessions:
            active_sessions[session_key] = SQLiteSession(
                session_id=session_key,
                db_path=_get_session_db_path()
            )
        session = active_sessions[session_key]
        
        logger.info(f"Using shared POST session: {session_key}")
    elif file_id:
        # Fallback: file-based session with client isolation
        session_id = f"{client_prefix}file_{file_id}_{SERVER_START_TIME}"
        
        session = SQLiteSession(
            session_id=session_id,
            db_path=_get_session_db_path()
        )
        
        cache_key = f"{client_prefix}{file_id}"
        active_sessions[cache_key] = session
        logger.info(f"Using client-isolated file session: {session_id}")
    else:
        # No session: Used for Summary/Next Steps (needs global context)
        logger.info(f"No session: Fresh context (for summary/next steps)")
    
    # Run agent using Runner with session (with retry for context limit)
    max_retries = 1
    retry_attempt = 0
    
    while retry_attempt <= max_retries:
        try:
            if retry_attempt > 0:
                # Retry with aggregate-only instruction
                logger.warning(f"Retry attempt {retry_attempt}/{max_retries} - NO SESSION (fresh start)")
                
                # Use no session for retry to avoid loading history.
                # This gives us a completely fresh start without any context
                session = None
                logger.info(f"Using NO session for retry (completely fresh)")
                
                # Add aggregate-only instruction
                agent_input = f"""{user_request_for_agent}

{dataset_runtime_context}

IMPORTANT: Please use ONLY aggregate queries to keep results small:
- Use COUNT, SUM, AVG, MIN, MAX with GROUP BY
- Avoid fetching raw data or large result sets  
- Focus on summaries and statistics only
- Keep responses concise
"""
            
            # Prepare input for Runner.run
            # If image is pending, use multimodal list format
            # Note: pending_image_data was declared global earlier in this function
            
            # Session to use for Runner.run
            run_session = session
            
            # Inject language instruction from client settings (supports Auto-detect)
            resolved_lang = resolve_language(client_id=client_id, message=message)
            lang_instruction = f"[LANGUAGE INSTRUCTION: You MUST respond in {resolved_lang}. All your output text must be in {resolved_lang}.]\n\n"

            if pending_image_data:
                # Multimodal input using OpenAI Responses API format
                run_input = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": f"{lang_instruction}{agent_input}"},
                            {"type": "input_image", "image_url": pending_image_data}
                        ]
                    }
                ]
                run_session = None  # Disable session for multimodal input
                logger.info(f"Using multimodal input with image (session disabled, image data size: {len(pending_image_data)} chars)")
                # Clear pending image after use
                pending_image_data = None
            else:
                # Text-only input with language instruction
                run_input = f"{lang_instruction}{agent_input}"
            
            logger.debug(f"Calling Runner.run with starting_agent, input, and session...")
            result = await Runner.run(
                starting_agent=agent, 
                input=run_input,
                max_turns=20,
                session=run_session
            )
            
            # Success - break out of retry loop
            break
            
        except Exception as e:
            # Check if it's a context length error
            import openai
            if isinstance(e, openai.BadRequestError) and "context_length_exceeded" in str(e):
                if retry_attempt < max_retries:
                    global MAX_TOOL_OUTPUT_SIZE, MAX_TOOL_ROWS
                    MAX_TOOL_OUTPUT_SIZE = max(50_000, MAX_TOOL_OUTPUT_SIZE // 2)
                    MAX_TOOL_ROWS = max(500, MAX_TOOL_ROWS // 2)
                    logger.warning(f"Context overflow, reducing tool output limits to {MAX_TOOL_OUTPUT_SIZE:,}B / {MAX_TOOL_ROWS} rows and retrying...")
                    retry_attempt += 1
                    run_session = None  # Drop accumulated session
                    continue
                else:
                    logger.error(f"Context limit exceeded even after {max_retries} retry attempts")
                    await ws_manager.emit_agent_typing(start_agent_name, post_id, "end", context=emit_context, client_id=client_id, role=agent_role)
                    raise RuntimeError(f"Context limit exceeded. The conversation history or query results are too large. Please start a new conversation or use more selective queries.")
            else:
                # Not a context limit error - raise immediately
                await ws_manager.emit_agent_typing(start_agent_name, post_id, "end", context=emit_context, client_id=client_id, role=agent_role)
                raise
    
    # Restore default limits after successful run (in case they were reduced)
    MAX_TOOL_OUTPUT_SIZE = 500_000
    MAX_TOOL_ROWS = 5000

    # Process result after successful execution
    try:
        
        # Minimal debug logging (detailed logs available via LOG_LEVEL=DEBUG)
        logger.debug(f"Runner result: {result.__class__.__name__}")
        logger.debug(f"Has final_output: {hasattr(result, 'final_output')}")
        logger.debug(f"Has new_items: {hasattr(result, 'new_items')}")
        
        # Extract the final agent and output from RunResult
        # The Runner handles handoffs automatically and returns the final result
        final_agent_name = agent.name
        response_content = ""
        
        # Try to get the final output from RunResult
        if hasattr(result, 'final_output'):
            response_content = str(result.final_output)
            logger.debug(f"Extracted final_output: {response_content[:200]}...")
        elif hasattr(result, 'data'):
            response_content = str(result.data)
            logger.debug(f"Extracted data: {response_content[:200]}...")
        elif hasattr(result, 'messages') and len(result.messages) > 0:
            # Get the last message content
            last_msg = result.messages[-1]
            if hasattr(last_msg, 'content'):
                response_content = last_msg.content
            else:
                response_content = str(last_msg)
            logger.debug(f"Extracted from messages: {response_content[:200]}...")
        else:
            # Fallback to string representation
            response_content = str(result)
            logger.warning(f"Using fallback str(result): {response_content[:200]}...")
        
        # Try to determine which agent actually responded (after handoffs)
        if hasattr(result, 'last_agent') and result.last_agent:
            if hasattr(result.last_agent, 'name'):
                final_agent_name = result.last_agent.name
            else:
                final_agent_name = str(result.last_agent)
            logger.info(f"Final agent after handoffs: {final_agent_name}")
        
        logger.info(f"Got response from {final_agent_name}: {response_content[:100]}...")
        
        # Log conversation summary (detailed logs via LOG_LEVEL=DEBUG)
        logger.debug(f"USER INPUT: {message[:100]}...")
        logger.debug(f"AGENT: {final_agent_name}")
        logger.debug(f"AGENT RESPONSE: {response_content[:100]}...")
        
        # Determine agent role from name
        agent_role = "insight"
        if "Statistical" in final_agent_name or "Analyst" in final_agent_name:
            agent_role = "statistics"
        elif "Visualization" in final_agent_name or "Visualizer" in final_agent_name:
            agent_role = "visualization"
        elif "Insights" in final_agent_name or "Insight" in final_agent_name or "Business" in final_agent_name:
            agent_role = "insight"
        elif "Summary" in final_agent_name or "Summar" in final_agent_name:
            agent_role = "summary"
        elif "Scout" in final_agent_name or "Scanner" in final_agent_name:
            agent_role = "scanner"
        
        # Use custom display name if set (client-aware)
        display_name = display_name if display_name else get_agent_display_name(agent_role, client_id)
        
        # Clean up the response content
        # Remove "RunResult:" prefix if present
        if "RunResult:" in response_content:
            response_content = response_content.split("RunResult:")[-1].strip()
        
        # If response is still too verbose or contains internal details, extract key content
        if len(response_content) > 1000 or "Last agent:" in response_content:
            # Try to extract just the meaningful content
            lines = response_content.split('\n')
            clean_lines = []
            for line in lines:
                # Skip internal details
                if any(skip in line for skip in ["Last agent:", "Final output", "new item(s)", "raw response", "guardrail", "RunResult", "See `"]):
                    continue
                clean_lines.append(line)
            if clean_lines:
                response_content = '\n'.join(clean_lines).strip()
        
        # Final fallback: if content is still not clean, provide a user-friendly message
        if not response_content or len(response_content) < 10:
            response_content = "Analysis in progress. The agent is processing your request."
            logger.warning(f"Could not extract clean content, using fallback message")
        
        # Extract visualization from tool calls if any
        logger.debug(f"Extracting visualization from new_items...")
        visualization_spec = None
        try:
            if hasattr(result, 'new_items') and result.new_items:
                logger.debug(f"Total new_items: {len(result.new_items)}")
                for idx, item in enumerate(result.new_items):
                    item_type = item.__class__.__name__
                    logger.debug(f"Item {idx}: {item_type}")
                    
                    # Log tool call info
                    if hasattr(item, 'raw_item') and item.raw_item:
                        raw = item.raw_item
                        if hasattr(raw, 'type') and raw.type == 'function_call':
                            logger.debug(f"Tool call: {raw.name if hasattr(raw, 'name') else 'unknown'}")
                    
                    # Check if it's a ToolCallOutputItem
                    if hasattr(item, 'output'):
                        result_content = item.output
                        # Log tool output preview
                        output_preview = str(result_content)[:200] if result_content else "None"
                        logger.debug(f"Tool output: {output_preview}...")
                        
                        if result_content:
                            try:
                                # Try to parse as JSON
                                if isinstance(result_content, str):
                                    tool_result = json.loads(result_content)
                                elif isinstance(result_content, dict):
                                    tool_result = result_content
                                else:
                                    continue
                                
                                # Check for error in tool result
                                if isinstance(tool_result, dict) and 'error' in tool_result:
                                    logger.warning(f"Tool returned error: {tool_result['error']}")
                                
                                # Check if it's a Vega-Lite spec
                                if isinstance(tool_result, dict) and '$schema' in tool_result:
                                    visualization_spec = tool_result
                                    logger.debug(f"Visualization found: {tool_result.get('title', 'Chart')}")
                                    # Debug: Log spec structure
                                    logger.debug(f"Vega spec keys: {list(tool_result.keys())}")
                                    if 'data' in tool_result:
                                        data_info = tool_result['data']
                                        if isinstance(data_info, dict) and 'values' in data_info:
                                            logger.debug(f"Vega data: {len(data_info['values'])} rows")
                                        else:
                                            logger.debug(f"Vega data type: {type(data_info)}")
                                    break
                            except (json.JSONDecodeError, Exception) as e:
                                logger.debug(f"Not a valid viz spec: {str(e)[:50]}")
            else:
                logger.debug("No new_items in result")
        except Exception as e:
            logger.error(f"Failed to extract visualization: {str(e)}", exc_info=True)

        # Always strip Vega blobs / markdown-fenced specs from text output
        if response_content:
            cleaned = _strip_vega_blobs(response_content)
            if cleaned != response_content:
                response_content = cleaned
                logger.info("Stripped raw Vega spec from response_content")

        # Strip backticks wrapping column names / short tokens
        response_content = _strip_backticks(response_content)

        # ========== WEBSOCKET: Emit typing end with display name ==========
        await ws_manager.emit_agent_typing(display_name, post_id, "end", context=emit_context, client_id=client_id, role=agent_role)
        
        return {
            "agent": display_name,
            "agent_role": agent_role,
            "content": response_content,
            "visualization": visualization_spec,
            "hitl_options": generate_hitl_options(agent_role, client_id=client_id)
        }
    except Exception as e:
        # ========== WEBSOCKET: Emit typing end on error ==========
        # Use emit_context if defined, else default to "reply"
        error_context = emit_context if 'emit_context' in dir() else "reply"
        error_agent_name = start_agent_name if 'start_agent_name' in dir() else "Data Analysis Team"
        await ws_manager.emit_agent_typing(error_agent_name, post_id, "end", context=error_context, client_id=client_id, role=agent_role)
        logger.error(f"Error processing agent result: {str(e)}", exc_info=True)
        raise RuntimeError(f"Agent result processing failed: {str(e)}")


def run_agent_analysis_sync(
    message: str,
    file_path: Optional[str] = None,
    file_id: Optional[str] = None,
    agent: Optional[Agent] = None
) -> Dict[str, Any]:
    """
    Synchronous version of run_agent_analysis
    
    Args:
        message: User message
        file_path: Path to file (if any)
        file_id: OpenAI file ID (if already uploaded)
        agent: Specific core agent to use. Defaults to the internal request router.
        
    Returns:
        Dict with agent response and metadata
    """
    import asyncio
    
    # Run async version in sync context
    return asyncio.run(run_agent_analysis(message, file_path, file_id, agent))


async def generate_summary(file_id: str, client_id: str = '') -> Dict[str, Any]:
    """
    Generate a summary of all conversation for a given file.
    
    Args:
        file_id: File ID to generate summary for
        client_id: Client ID for isolation
        
    Returns:
        Dict with summary content and next_steps in markdown format
    """
    from utils.logger import logger
    
    try:
        # Set client context for tool functions
        if client_id:
            current_client_id.set(client_id)
        
        logger.info(f"Generating summary for file: {file_id}, client: {client_id[:20] if client_id else 'N/A'}...")
        
        # Get data schema information for context
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
            logger.info(f"Including data schema: {len(columns)} columns, {rows:,} rows")
        
        # Generate summary
        summary_lang = resolve_language(client_id=client_id)
        
        summary_result = await Runner.run(
            starting_agent=summary_writer,
            input=f"[LANGUAGE: Respond in {summary_lang}] Generate a concise summary of the conversation about file_id: {file_id}"
        )
        
        summary_content = ""
        if hasattr(summary_result, 'final_output'):
            summary_content = summary_result.final_output
        
        logger.info(f"Summary generated: {len(summary_content)} chars")
        
        # Generate next steps with data schema context
        next_step_prompt = build_summary_next_steps_prompt(
            language=summary_lang,
            file_id=file_id,
            data_schema_info=data_schema_info,
        )
        next_step_result = await Runner.run(
            starting_agent=next_step_generator,
            input=next_step_prompt
        )
        
        next_steps_data = []
        if hasattr(next_step_result, 'final_output'):
            next_step_content = next_step_result.final_output
            
            # Try to parse as JSON
            try:
                # Remove markdown code blocks if present
                if "```json" in next_step_content:
                    next_step_content = next_step_content.split("```json")[1].split("```")[0].strip()
                elif "```" in next_step_content:
                    next_step_content = next_step_content.split("```")[1].split("```")[0].strip()
                
                parsed = json.loads(next_step_content)
                next_steps_data = parsed.get("next_steps", [])
                from services.next_step_routing import infer_next_step_target_agent_role, normalize_target_agent_role
                normalized_steps = []
                for step in next_steps_data:
                    if not isinstance(step, dict):
                        continue
                    question = step.get("question") or step.get("title") or ""
                    step["target_agent_role"] = (
                        normalize_target_agent_role(step.get("target_agent_role"))
                        or infer_next_step_target_agent_role(question)
                    )
                    normalized_steps.append(step)
                next_steps_data = normalized_steps
                logger.info(f"Next steps generated: {len(next_steps_data)} cards")
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to parse next steps as JSON: {str(e)}")
                # Fallback to empty list
                next_steps_data = []
        
        return {
            "success": True,
            "file_id": file_id,
            "content": summary_content,
            "next_steps": next_steps_data,
            "agent": "Summary Writer"
        }
        
    except Exception as e:
        logger.error(f"Failed to generate summary: {str(e)}", exc_info=True)
        return {
            "success": False,
            "file_id": file_id,
            "error": str(e)
        }


# ==================== Helper Functions ====================

def generate_hitl_options(agent_role: str, client_id: str = None) -> List[str]:
    """Generate Human-in-the-Loop follow-up options (language-aware)"""
    lang = resolve_language(client_id=client_id)
    
    if lang == "Korean":
        options_map = {
            "statistics": [
                "시각화로 보여줘",
                "비즈니스적으로 어떤 의미야?",
                "데이터를 더 깊이 분석해줘"
            ],
            "visualization": [
                "다른 차트 유형으로 보여줘",
                "어떤 인사이트가 있어?",
                "숫자로 알려줘"
            ],
            "insight": [
                "통계로 뒷받침해줘",
                "이 인사이트를 시각화해줘",
                "다음에 뭘 해야 해?"
            ]
        }
    else:
        options_map = {
            "statistics": [
                "Show me a visualization",
                "What does this mean for business?",
                "Dig deeper into the data"
            ],
            "visualization": [
                "Show me different chart type",
                "What are the insights?",
                "Give me the numbers"
            ],
            "insight": [
                "Show supporting statistics",
                "Visualize this insight",
                "What should I do next?"
            ]
        }
    
    return options_map.get(agent_role, [])


def get_agent_by_role(role: str) -> Agent:
    """Get agent by role name"""
    role_map = {
        "statistics": analyst_agent,
        "visualization": visualizer_agent,
        "insight": insight_agent,
        "summary": summary_feed_agent,
        "scanner": proactive_scanner_agent,
    }
    return role_map.get(role, request_router)


# ==================== @Mention Support ====================
# Map @keywords to agent roles for direct calling

MENTION_MAP = {
    "@statistical": "statistics",
    "@stats": "statistics",
    "@stat": "statistics",
    "@analyst": "statistics",
    "@visualization": "visualization",
    "@visualize": "visualization",
    "@viz": "visualization",
    "@chart": "visualization",
    "@insight": "insight",
    "@insights": "insight",
    "@intelligence": "insight",
    "@summary": "summary",
    "@summarize": "summary",
    "@synthesize": "summary",
    "@narrator": "summary",
    "@data": "scanner",
    "@datascout": "scanner",
    "@scout": "scanner",
    "@scanner": "scanner",
    "@explore": "scanner"
}

def parse_mentions(text: str, client_id: str = None) -> tuple[Optional[str], Optional[str], str]:
    """
    Parse @mentions from text and extract mentioned agent role and specific agent.
    
    Args:
        text: User message text
        client_id: Client ID for looking up custom agent names
        
    Returns:
        Tuple of (agent_role or None, agent_id or None, cleaned_text with mention removed)
        
    """
    import re
    from utils.logger import logger

    def tidy_cleaned_text(value: str) -> str:
        value = re.sub(r'(^|[\r\n])\s*,\s*', r'\1', value).strip()
        return re.sub(r'\s{2,}', ' ', value)
    
    normalized_text = text.replace('＠', '@')

    # Find @mention pattern (case-insensitive).
    # Accept "@Statistical" and the common typo "@ Statistical".
    mention_pattern = r'@\s*(\w+)'
    matches = re.findall(mention_pattern, normalized_text, re.IGNORECASE)
    
    if not matches:
        return None, None, text
    
    # Check each match against MENTION_MAP first (role keywords)
    for match in matches:
        mention_key = f"@{match.lower()}"
        if mention_key in MENTION_MAP:
            agent_role = MENTION_MAP[mention_key]
            # Remove mention from text
            suffix_by_role = {
                "statistics": r'(?:\s+analyst)?',
                "visualization": r'(?:\s+expert)?',
                "insight": r'(?:\s+agent)?',
                "summary": r'(?:\s+agent)?',
                "scanner": r'(?:\s+scout|\s+agent)?',
            }
            suffix_pattern = suffix_by_role.get(agent_role, "")
            cleaned_text = re.sub(
                rf'@\s*{re.escape(match)}{suffix_pattern}\s*',
                '',
                normalized_text,
                count=1,
                flags=re.IGNORECASE,
            ).strip()
            if agent_role == "statistics":
                cleaned_text = re.sub(r'^analyst\b[,\s]*', '', cleaned_text, count=1, flags=re.IGNORECASE).strip()
            elif agent_role == "visualization":
                cleaned_text = re.sub(r'^expert\b[,\s]*', '', cleaned_text, count=1, flags=re.IGNORECASE).strip()
            elif agent_role in {"summary", "insight", "scanner"}:
                cleaned_text = re.sub(r'^agent\b[,\s]*', '', cleaned_text, count=1, flags=re.IGNORECASE).strip()
            return agent_role, None, tidy_cleaned_text(cleaned_text)
    
    # If no role keyword matched, check for agent names in client's registry
    if client_id:
        try:
            from services.client_store import get_client_store
            store = get_client_store(client_id)
            
            for match in matches:
                match_lower = match.lower()
                # Search agent registry for matching name
                for agent_id, agent in store.agent_registry.items():
                    agent_name = agent.get("name", "")
                    # Match against first word of name or full name
                    name_parts = agent_name.lower().split()
                    if name_parts and (match_lower == name_parts[0] or match_lower == agent_name.lower().replace(" ", "")):
                        # Found matching agent by name
                        remaining_suffix = ''.join(rf'(?:\s+{re.escape(part)})?' for part in name_parts[1:])
                        cleaned_text = re.sub(
                            rf'@\s*{re.escape(match)}{remaining_suffix}\s*',
                            '',
                            normalized_text,
                            count=1,
                            flags=re.IGNORECASE,
                        ).strip()
                        logger.info(f"Matched @{match} to agent '{agent_name}' (id={agent_id}, role={agent['role']})")
                        return agent["role"], agent_id, tidy_cleaned_text(cleaned_text)
        except Exception as e:
            logger.warning(f"Failed to lookup agent by name: {e}")
    
    return None, None, normalized_text


def get_available_mentions() -> List[Dict[str, str]]:
    """
    Get list of available @mentions for frontend autocomplete.
    
    Returns:
        List of {keyword, role, display_name} dicts
    """
    # Deduplicate by role and pick best keyword
    role_to_mention = {}
    for keyword, role in MENTION_MAP.items():
        if role not in role_to_mention:
            role_to_mention[role] = keyword
    
    display_names = {
        "statistics": "Statistical Analyst",
        "visualization": "Visualization Expert", 
        "insight": "Intelligence",
        "scanner": "Data Scout"
    }
    
    return [
        {
            "keyword": keyword,
            "role": role,
            "display_name": display_names.get(role, role.title())
        }
        for role, keyword in role_to_mention.items()
    ]


# ==================== Post Reference System (#N Mentions) ====================
# Allows users to reference other posts in their messages using #N syntax

def parse_post_references(text: str) -> tuple[List[int], str]:
    """
    Parse #N post references from text.
    
    Args:
        text: User message text
        
    Returns:
        Tuple of (list of post IDs, cleaned text with references removed)
    """
    import re
    from utils.logger import logger
    
    # Match #1, #2, #12, etc. (but not # followed by letters)
    pattern = r'#(\d+)'
    matches = re.findall(pattern, text)
    
    if not matches:
        return [], text
    
    # Convert to integers and remove duplicates while preserving order
    post_ids = []
    seen = set()
    for match in matches:
        post_id = int(match)
        if post_id not in seen:
            post_ids.append(post_id)
            seen.add(post_id)
    
    # Limit to MAX_REFERENCE_POSTS
    if len(post_ids) > MAX_REFERENCE_POSTS:
        logger.warning(f"Too many post references ({len(post_ids)}), limiting to {MAX_REFERENCE_POSTS}")
        post_ids = post_ids[:MAX_REFERENCE_POSTS]
    
    # Clean text (remove #N references)
    cleaned_text = re.sub(r'#\d+\s*', '', text).strip()
    
    logger.info(f"Parsed post references: {post_ids} from '{text[:50]}...'")
    
    return post_ids, cleaned_text


def get_post_summary(post_id: int, client_id: str) -> Optional[str]:
    """
    Get or generate a summary for a post.
    
    Args:
        post_id: Post ID to summarize
        client_id: Client ID for isolation
        
    Returns:
        Summary string or None if post not found
    """
    from utils.logger import logger
    from services.client_store import get_client_store
    
    try:
        store = get_client_store(client_id)
        
        # Find the post
        post = None
        for p in store.posts_db:
            if p and p.get('id') == post_id:
                post = p
                break
        
        if not post:
            logger.warning(f"Post #{post_id} not found for reference")
            return None
        
        # Build summary from post content and replies
        summary_parts = []
        
        # Post content
        post_content = truncate_text(
            post.get('content', ''),
            REFERENCE_POST_BODY_TOKEN_BUDGET,
            preserve="head"
        )
        author = post.get('author', 'User')
        summary_parts.append(f"[Original Post by {author}]\n{post_content}")
        
        # Summarize replies
        replies = post.get('replies', [])
        if replies:
            summary_parts.append(f"\n[{len(replies)} Agent Replies]")
            for reply in replies[:3]:  # First 3 replies
                reply_author = reply.get('author', 'Agent')
                reply_content = truncate_text(
                    reply.get('content', ''),
                    REFERENCE_REPLY_TOKEN_BUDGET,
                    preserve="head"
                )
                summary_parts.append(f"- {reply_author}: {reply_content}")
            
            if len(replies) > 3:
                summary_parts.append(f"  ... and {len(replies) - 3} more replies")
        
        summary = truncate_text(
            "\n".join(summary_parts),
            REFERENCE_POST_TOKEN_BUDGET,
            preserve="middle"
        )
        
        logger.info(f"Generated summary for Post #{post_id}: {len(summary)} chars")
        return summary
        
    except Exception as e:
        logger.error(f"Failed to get post summary: {e}")
        return None


def build_reference_context(post_ids: List[int], client_id: str) -> str:
    """
    Build context from referenced posts with token budget limits.
    
    Args:
        post_ids: List of post IDs to reference
        client_id: Client ID for isolation
        
    Returns:
        Formatted reference context string
    """
    from utils.logger import logger
    
    if not post_ids:
        return ""
    
    context_parts = []
    
    for post_id in post_ids:
        summary = get_post_summary(post_id, client_id)
        if not summary:
            continue
        
        reference = f"\n[Referenced Context from Post #{post_id}]\n{summary}\n[End of Reference]\n"
        context_parts.append(ContextBlock(
            name=f"post_{post_id}",
            content=reference,
            token_budget=REFERENCE_POST_TOKEN_BUDGET,
            preserve="middle"
        ))
    
    final_context = assemble_context(
        context_parts,
        token_budget=REFERENCE_CONTEXT_TOKEN_BUDGET,
        separator="\n"
    )
    logger.info(f"Built reference context: ~{estimate_tokens(final_context)} tokens for {len(context_parts)} posts")
    
    return final_context


# ==================== Agent Discussion Feature ====================
# Agents evaluate their own relevance and respond if they have something valuable to add

DISCUSSION_AGENTS = {
    "statistics": analyst_agent,
    "visualization": visualizer_agent,
    "insight": insight_agent,
    "summary": summary_feed_agent,
}

# ==================== Agent @Mention System (Agent-Agent Collaboration) ====================
# Allows agents to request help from other agents using @mention syntax

# Mapping of @mention patterns to agent roles
AGENT_MENTION_PATTERNS = {
    "statistics": ["@statistical analyst", "@statistics", "@analyst", "@stat"],
    "visualization": ["@visualization expert", "@visualization", "@visualizer", "@viz", "@chart"],
    "insight": ["@intelligence agent", "@intelligence", "@insights", "@insight"],
    "summary": ["@summary agent", "@summary", "@summarize", "@synthesize", "@narrator"],
}

def _parse_agent_mention(content: str) -> Optional[str]:
    """
    Parse agent @mention from response content.
    Returns the role of the mentioned agent, or None if no mention found.
    
    """
    content_lower = content.lower()
    
    for role, patterns in AGENT_MENTION_PATTERNS.items():
        for pattern in patterns:
            if pattern in content_lower:
                return role
    
    return None

def _get_mention_context(content: str, mentioned_role: str) -> str:
    """
    Extract the request context around the @mention.
    This helps the mentioned agent understand what's being asked.
    """
    content_lower = content.lower()
    
    for pattern in AGENT_MENTION_PATTERNS.get(mentioned_role, []):
        if pattern in content_lower:
            # Find the sentence containing the mention
            idx = content_lower.find(pattern)
            # Get surrounding context (100 chars before and after)
            start = max(0, idx - 100)
            end = min(len(content), idx + len(pattern) + 150)
            return content[start:end].strip()
    
    return ""


async def analyze_mention_intent(content: str, mentioned_role: str) -> dict:
    """
    Use LLM to analyze whether an @mention is a REQUEST (asking for help)
    or a QUOTE/REFERENCE (just mentioning what the agent said).
    
    Args:
        content: The full text containing the @mention
        mentioned_role: The role of the mentioned agent
        
    Returns:
        {
            "is_request": True/False,
            "intent": "request" | "quote" | "reference",
            "confidence": 0.0-1.0
        }
    """
    from utils.logger import logger
    from openai import AsyncOpenAI
    import json
    
    # Get the mention context for analysis
    mention_context = _get_mention_context(content, mentioned_role)
    if not mention_context:
        mention_context = truncate_text(content, 90, preserve="head")
    
    prompt = build_mention_intent_prompt(mention_context, mentioned_role)

    try:
        # Use AsyncOpenAI directly for simple LLM calls
        async_client = AsyncOpenAI()
        
        response = await async_client.chat.completions.create(
            model=settings.UTILITY_MODEL,  # Fast and cheap for simple classification
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=100,
        )
        
        # Parse response
        response_text = response.choices[0].message.content.strip()
        
        result = json.loads(response_text)
        result["is_request"] = result.get("intent") == "request"
        
        logger.info(f"Mention intent analysis: {mentioned_role} → {result['intent']} (confidence: {result.get('confidence', 0)})")
        return result
        
    except Exception as e:
        logger.warning(f"Mention intent analysis failed: {e}")
        # Default to treating it as a request (safe fallback)
        return {
            "is_request": True,
            "intent": "request",
            "confidence": 0.5
        }


async def evaluate_discussion_relevance(
    agent_role: str,
    previous_response: str,
    original_context: str = "",
    image_attached: bool = False
) -> dict:
    """
    Ask an agent to evaluate how relevant/valuable their contribution would be.
    Returns dict with score (0-10) and brief reason.
    """
    from utils.logger import logger
    
    agent = DISCUSSION_AGENTS.get(agent_role)
    if not agent:
        return {"score": 0, "reason": "Unknown agent"}
    
    eval_prompt = build_discussion_relevance_prompt(
        agent.name,
        previous_response,
        image_attached=image_attached,
    )
    
    try:
        # Create a simple evaluation agent (no handoffs, just returns JSON)
        eval_agent = Agent(
            name=f"{agent.name} Evaluator",
            instructions=DISCUSSION_RELEVANCE_EVALUATOR_INSTRUCTIONS,
            model=settings.get_utility_model(),
            model_settings=UTILITY_JSON_MODEL_SETTINGS,
            tools=[],  # No tools needed for simple evaluation
        )
        
        result = await Runner.run(
            starting_agent=eval_agent,
            input=eval_prompt,
            max_turns=3  # Allow enough turns for simple response
        )
        
        response_text = (result.final_output or "").strip()
        
        # Parse JSON response
        import json
        import re
        
        # Extract JSON from response
        json_match = re.search(r'\{[^}]+\}', response_text)
        if json_match:
            data = json.loads(json_match.group())
            score = int(data.get("score", 0))
            reason = data.get("reason", "")
            logger.info(f"{agent.name} eval: {score}/10 - {reason}")
            return {"score": score, "reason": reason, "agent_role": agent_role}
        
        return {"score": 0, "reason": "Could not parse", "agent_role": agent_role}
        
    except Exception as e:
        logger.warning(f"Eval failed for {agent_role}: {e}")
        return {"score": 0, "reason": str(e), "agent_role": agent_role}


async def generate_discussion_reply(
    agent_role: str,
    previous_response: str,
    file_id: str,
    original_context: str = "",
    post_id: Optional[int] = None,  # POST 기반 세션을 위한 post_id
    agent_name: Optional[str] = None,  # Specific agent name (for multi-agent per role)
    client_id: Optional[str] = None,  # Client ID for session isolation
    image_file_path: Optional[str] = None  # Image file for Vision API (multimodal)
) -> Optional[Dict[str, Any]]:
    """
    Generate a discussion reply from the specified agent.
    Uses POST-based session to prevent context bleeding between posts.
    """
    from utils.logger import logger
    
    agent = DISCUSSION_AGENTS.get(agent_role)
    if not agent:
        return None
    
    # Use specific agent name if provided, otherwise use default
    display_name = agent_name or agent.name
    
    # Role-to-description mapping for self-identification
    ROLE_DESCRIPTIONS = {
        "statistics": "Statistical Analyst (you respond to @Statistical, @Analyst, @Statistics mentions)",
        "visualization": "Visualization Expert (you respond to @Visualization, @Viz, @Chart mentions)",
        "insight": "Intelligence Specialist (you respond to @Intelligence, @Insight mentions)",
        "summary": "Summary Agent (you respond to @Summary, @Summarize mentions) - you synthesize findings from other agents into cohesive narratives",
        "scanner": "Data Scout (you respond to @Scout, @Scanner mentions)"
    }
    role_description = ROLE_DESCRIPTIONS.get(agent_role, agent_role.title())
    
    # Build image context notice
    image_notice = ""
    if image_file_path:
        image_notice = build_discussion_image_notice()
    
    def _compact_discussion_context(text: str, token_budget: int = DISCUSSION_CONTEXT_TOKEN_BUDGET) -> str:
        return truncate_text(text, token_budget, preserve="middle")

    def _build_discussion_prompt(context_text: str) -> str:
        return build_discussion_reply_prompt(
            context_text=context_text,
            image_notice=image_notice,
            display_name=display_name,
            role_description=role_description,
            agent_role=agent_role,
            file_id=file_id,
        )

    discussion_prompt = _build_discussion_prompt(_compact_discussion_context(previous_response))
    
    try:
        # Clear stale data scope so discussion agents can query freely
        # without being locked to filters extracted from the original user message
        global current_data_scope, MAX_TOOL_OUTPUT_SIZE, MAX_TOOL_ROWS
        current_data_scope = {}
        
        # Each discussion reply gets a fresh session to prevent context accumulation.
        session = None
        if post_id is not None:
            global active_sessions
            import time
            session_key = f"post_{post_id}_{agent_role}_{client_id}_{int(time.time())}"
            session = SQLiteSession(
                session_id=session_key,
                db_path=_get_session_db_path()
            )
            logger.debug(f"Fresh discussion session: {session_key}")
        
        # ============ LANGUAGE INSTRUCTION ============
        resolved_lang = resolve_language(client_id=client_id, message=original_context)
        lang_prefix = f"[LANGUAGE INSTRUCTION: You MUST respond in {resolved_lang}. All your output text must be in {resolved_lang}.]\n\n"
        
        # ============ MULTIMODAL INPUT (Image + Text) ============
        image_data_url = None
        
        if image_file_path:
            try:
                import base64
                with open(image_file_path, 'rb') as img_file:
                    img_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                
                file_ext = image_file_path.lower()[image_file_path.rfind('.'):]
                mime_map = {
                    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'
                }
                mime_type = mime_map.get(file_ext, 'image/png')
                image_data_url = f"data:{mime_type};base64,{img_base64}"
                
                logger.info(f"Discussion reply using multimodal input with image (base64 size: {len(image_data_url)} chars)")
            except Exception as e:
                logger.warning(f"Failed to load image for discussion: {e}")

        def _build_run_input(prompt_text: str, use_session):
            if image_data_url:
                return [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": f"{lang_prefix}{prompt_text}"},
                            {"type": "input_image", "image_url": image_data_url}
                        ]
                    }
                ], None
            return f"{lang_prefix}{prompt_text}", use_session

        run_input, run_session = _build_run_input(discussion_prompt, session)
        reply_agent = agent.clone(model_settings=DISCUSSION_REPLY_MODEL_SETTINGS)
        
        last_error = None
        
        for attempt, token_budget in enumerate(DISCUSSION_CONTEXT_RETRY_BUDGETS):
            try:
                if attempt > 0:
                    # Shrink tool output limits each retry
                    MAX_TOOL_OUTPUT_SIZE = max(50_000, MAX_TOOL_OUTPUT_SIZE // 2)
                    MAX_TOOL_ROWS = max(500, MAX_TOOL_ROWS // 2)
                    # Rebuild prompt with shorter context
                    discussion_prompt = _build_discussion_prompt(
                        _compact_discussion_context(
                            previous_response,
                            token_budget=token_budget,
                        )
                    )
                    run_session = None  # Drop session on retry
                    run_input, run_session = _build_run_input(discussion_prompt, run_session)
                    logger.info(f"Retry {attempt}: context≈{token_budget} tokens, tool output→{MAX_TOOL_OUTPUT_SIZE:,}B, no session")
                
                result = await Runner.run(
                    starting_agent=reply_agent,
                    input=run_input,
                    max_turns=20,
                    session=run_session
                )
                break  # Success
                
            except Exception as e:
                last_error = e
                error_text = str(e)
                if (
                    "context_length_exceeded" in error_text
                    or "rate_limit_exceeded" in error_text
                    or "tokens per min" in error_text
                ):
                    logger.warning(f"Discussion reply attempt {attempt+1} failed with size/rate limit, retrying shorter...")
                    run_session = None
                    import asyncio
                    await asyncio.sleep(min(2 * (attempt + 1), 5))
                    continue
                else:
                    raise  # Non-context errors should propagate
        else:
            logger.error(f"Discussion reply failed after {len(DISCUSSION_CONTEXT_RETRY_BUDGETS)} attempts: {last_error}")
            MAX_TOOL_OUTPUT_SIZE = 500_000
            MAX_TOOL_ROWS = 5000
            return None
        
        # Restore default limits after success
        MAX_TOOL_OUTPUT_SIZE = 500_000
        MAX_TOOL_ROWS = 5000

        response_text = (result.final_output or "").strip()
        
        # Extract visualization if any (for Visualization Expert)
        visualization = None
        try:
            if hasattr(result, 'new_items') and result.new_items:
                for item in result.new_items:
                    if hasattr(item, 'output'):
                        result_content = item.output
                        if result_content:
                            try:
                                if isinstance(result_content, str):
                                    tool_result = json.loads(result_content)
                                elif isinstance(result_content, dict):
                                    tool_result = result_content
                                else:
                                    continue
                                if isinstance(tool_result, dict) and '$schema' in tool_result:
                                    visualization = tool_result
                                    logger.info(f"Discussion reply includes visualization: {tool_result.get('title', 'Chart')}")
                                    break
                            except (json.JSONDecodeError, Exception):
                                pass
        except Exception as e:
            logger.debug(f"Failed to extract visualization from discussion reply: {e}")

        # Always strip Vega blobs / markdown-fenced specs from text output
        if response_text:
            cleaned = _strip_vega_blobs(response_text)
            if cleaned != response_text:
                response_text = cleaned
                logger.info("Stripped raw Vega spec from discussion response_text")

        # Strip backticks wrapping column names / short tokens
        response_text = _strip_backticks(response_text)

        # Use custom display name (client-aware)
        display_name = get_agent_display_name(agent_role, client_id)
        
        logger.info(f"{display_name} discussion reply: {response_text[:100]}...")
        
        return {
            "content": response_text,
            "agent_name": display_name,
            "agent_role": agent_role,
            "visualization": visualization
        }
        
    except Exception as e:
        logger.error(f"Discussion reply failed: {e}")
        return None


async def trigger_agent_discussion(
    previous_response: str,
    responding_agent: str,
    file_id: str,
    original_context: str = "",
    threshold: int = 9,
    post_id: Optional[int] = None,  # POST 기반 세션을 위한 post_id
    on_reply_generated: Optional[Callable] = None,  # 실시간 reply 처리 콜백
    client_id: Optional[str] = None,
    image_file_path: Optional[str] = None  # Image file for Vision API (multimodal)
) -> List[Dict[str, Any]]:
    """
    Role-based agent discussion with same-role follow-up comments.
    
    Structure:
        1. Evaluate ALL agents (all roles, all agents per role)
        2. Select highest scorer → respond
        3. Same-role agents can add follow-up comments (if threshold met)
        4. Move to next role (highest scorer among remaining roles)
        5. Repeat until all roles processed
    
    Args:
        previous_response: The response that might trigger discussion
        responding_agent: The agent role that gave the previous response (to exclude)
        file_id: Dataset file ID
        original_context: Original user message/context
        threshold: Minimum score for follow-up comments (default 9)
        post_id: POST ID for session isolation
        client_id: Client ID for session isolation
        on_reply_generated: Async callback for real-time reply emission
    
    Returns:
        List of discussion reply dicts
    """
    from utils.logger import logger
    from services.websocket_manager import ws_manager
    import asyncio
    
    # ==================== #POST REFERENCE CONTEXT INJECTION ====================
    reference_context = ""
    if client_id and original_context:
        ref_post_ids, cleaned_context = parse_post_references(original_context)
        if ref_post_ids:
            reference_context = build_reference_context(ref_post_ids, client_id)
            if reference_context:
                logger.info(f"Discussion: Injecting reference context from posts: {ref_post_ids}")
                previous_response = (
                    f"[USER REQUEST — THIS IS WHAT YOU MUST ANSWER]\n{cleaned_context}\n\n"
                    f"[Referenced Post(s) #{', #'.join(str(i) for i in ref_post_ids)}]\n{reference_context}\n\n"
                    f"[Current Post Discussion]\n{previous_response}"
                )
    
    # Priority order when scores are equal: statistics > insight > visualization > summary
    ROLE_PRIORITY = {
        "statistics": 0,
        "insight": 1,
        "visualization": 2,
        "summary": 3
    }
    
    # Role-specific thresholds (lowered to encourage cross-agent reactions)
    ROLE_THRESHOLD = {
        "statistics": threshold - 1,  # Default 8 (react to agent findings)
        "insight": threshold - 1,     # Default 8 (react with context/search)
        "visualization": 7,           # Lower threshold (react by visualizing)
        "summary": threshold,         # Default 9 (only when enough multi-agent content to synthesize)
    }
    
    # Threshold for same-role follow-up comments
    FOLLOWUP_THRESHOLD = 9  # Same-role follow-up requires high relevance
    
    # ==================== @MENTION TRACKING (Abuse Prevention) ====================
    MAX_MENTIONS_PER_DISCUSSION = 3  # Max @mentions allowed in one discussion
    mention_count = 0                 # Track total @mentions used
    pending_mention = None            # Role that was @mentioned (to be called next)
    last_responding_role = None       # Prevent A→B→A mention loops
    responded_roles = set()           # Track roles that already responded
    
    # Get all roles except the one that just responded
    remaining_roles = [role for role in DISCUSSION_AGENTS.keys() if role != responding_agent]
    
    if not remaining_roles:
        return []
    
    replies = []
    # If image is attached, prepend notice to conversation context for evaluators
    if image_file_path:
        conversation_context = f"[NOTE: An image is attached to this discussion. Agents should analyze the image content.]\n\n{previous_response}"
    else:
        conversation_context = previous_response
    role_round = 1
    is_first_response = True  # Track if this is the very first response
    
    has_image = image_file_path is not None
    
    while remaining_roles:
        logger.info(f"Role Round {role_round}: Evaluating roles {remaining_roles}")
        
        # ==================== CHECK FOR PENDING @MENTION ====================
        # If previous agent @mentioned someone, skip evaluation and call them directly
        if pending_mention and pending_mention in remaining_roles:
            selected_role = pending_mention
            logger.info(f"Role Round {role_round}: {selected_role} selected via @mention (evaluation skipped)")
            pending_mention = None  # Clear the pending mention
        else:
            # Normal flow: evaluate all remaining roles
            
            # 1. Evaluate one representative from each remaining role
            eval_tasks = [
                evaluate_discussion_relevance(role, conversation_context, original_context, image_attached=has_image)
                for role in remaining_roles
            ]
            
            evaluations = await asyncio.gather(*eval_tasks)
            
            # 2. Sort by score (desc), then by priority (asc) when tied
            evaluations.sort(
                key=lambda x: (-x.get("score", 0), ROLE_PRIORITY.get(x.get("agent_role"), 99))
            )
            
            if not evaluations:
                break
            
            # 3. Filter evaluations by role-specific threshold (except first response)
            if is_first_response:
                # First response: just pick highest score
                top_eval = evaluations[0]
                top_score = top_eval.get("score", 0)
                selected_role = top_eval.get("agent_role")
                logger.info(f"Role Round {role_round}: {selected_role} selected ({top_score}/10) - mandatory first response")
                is_first_response = False
            else:
                # Round 2+: Filter by each role's threshold, then pick highest
                passing_evals = [
                    e for e in evaluations 
                    if e.get("score", 0) >= ROLE_THRESHOLD.get(e.get("agent_role"), threshold)
                ]
                
                if not passing_evals:
                    # Log why we're stopping
                    top_eval = evaluations[0]
                    top_score = top_eval.get("score", 0)
                    top_role = top_eval.get("agent_role")
                    top_threshold = ROLE_THRESHOLD.get(top_role, threshold)
                    logger.info(f"Role Round {role_round}: No role meets threshold. Best: {top_role} ({top_score}/10 < {top_threshold})")
                    break
                
                # Pick highest from passing evaluations
                top_eval = passing_evals[0]
                top_score = top_eval.get("score", 0)
                selected_role = top_eval.get("agent_role")
                role_threshold = ROLE_THRESHOLD.get(selected_role, threshold)
                logger.info(f"Role Round {role_round}: {selected_role} selected ({top_score}/10, threshold={role_threshold})")
        
        # 4. Get all agents of this role (client-aware)
        same_role_agents = get_agents_by_role(selected_role, client_id)
        logger.info(f"{selected_role} has {len(same_role_agents)} agent(s)")
        
        # 5. First agent of this role responds (primary response)
        primary_agent = same_role_agents[0] if same_role_agents else None
        
        if primary_agent:
            display_name = primary_agent["name"]
            
            # Emit typing start
            await ws_manager.emit_agent_typing(display_name, post_id, "start", context="reply", client_id=client_id, role=selected_role)
            logger.info(f"{display_name} is typing...")
            
            # Generate response
            reply = await generate_discussion_reply(
                selected_role,
                conversation_context,
                file_id,
                original_context,
                post_id=post_id,
                agent_name=display_name,  # Pass specific agent name
                client_id=client_id,  # Pass client_id for isolation
                image_file_path=image_file_path  # Pass image for multimodal
            )
            
            # Emit typing end
            await ws_manager.emit_agent_typing(display_name, post_id, "end", context="reply", client_id=client_id, role=selected_role)
            
            if reply:
                reply["agent_name"] = display_name  # Override with specific name
                replies.append(reply)
                conversation_context += f"\n\n{display_name}: {reply['content']}"
                logger.info(f"{display_name} (primary) responded")
                
                if on_reply_generated:
                    try:
                        await on_reply_generated(reply)
                    except Exception as cb_err:
                        logger.warning(f"Callback failed: {cb_err}")
                
                # ==================== AGENT @MENTION DETECTION ====================
                # Check if this agent mentioned another agent for collaboration
                mentioned_role = _parse_agent_mention(reply.get("content", ""))
                
                if mentioned_role:
                    # Validate @mention (abuse prevention)
                    can_mention = True
                    skip_reason = None
                    
                    # Rule 1: Can't @mention yourself
                    if mentioned_role == selected_role:
                        can_mention = False
                        skip_reason = "self-mention not allowed"
                    
                    # Rule 2: Can't @mention already responded roles
                    elif mentioned_role in responded_roles:
                        can_mention = False
                        skip_reason = f"{mentioned_role} already responded"
                    
                    # Rule 3: Can't @mention if not in remaining roles
                    elif mentioned_role not in remaining_roles:
                        can_mention = False
                        skip_reason = f"{mentioned_role} not available"
                    
                    # Rule 4: Can't exceed max @mentions per discussion
                    elif mention_count >= MAX_MENTIONS_PER_DISCUSSION:
                        can_mention = False
                        skip_reason = f"max {MAX_MENTIONS_PER_DISCUSSION} @mentions reached"
                    
                    # Rule 5: Can't create A→B→A loops (last responder can't be mentioned)
                    elif mentioned_role == last_responding_role:
                        can_mention = False
                        skip_reason = "mention loop prevented"
                    
                    if can_mention:
                        # LLM-based intent analysis: is this a REQUEST or just a QUOTE?
                        intent_result = await analyze_mention_intent(
                            reply.get("content", ""), 
                            mentioned_role
                        )
                        
                        if intent_result["is_request"] and intent_result.get("confidence", 0) >= 0.6:
                            # It's a genuine request → set pending mention
                            pending_mention = mentioned_role
                            mention_count += 1
                            mention_context = _get_mention_context(reply.get("content", ""), mentioned_role)
                            logger.info(f"Agent collaboration: {display_name} → @{mentioned_role} ({mention_count}/{MAX_MENTIONS_PER_DISCUSSION})")
                            logger.info(f"Intent: REQUEST (confidence: {intent_result.get('confidence', 0):.1%})")
                            logger.info(f"Context: {mention_context[:80]}...")
                        else:
                            # It's a quote/reference → ignore
                            logger.info(f"@mention ignored: {intent_result['intent']} (not a request)")
                    else:
                        logger.info(f"@mention ignored: {skip_reason}")
        
        # 6. Same-role agents can add follow-up comments
        if len(same_role_agents) > 1:
            for followup_agent in same_role_agents[1:]:
                followup_name = followup_agent["name"]
                
                # Evaluate if this agent wants to add something
                followup_eval = await evaluate_discussion_relevance(
                    selected_role, conversation_context, original_context, image_attached=has_image
                )
                followup_score = followup_eval.get("score", 0)
                
                if followup_score >= FOLLOWUP_THRESHOLD:
                    logger.info(f"{followup_name} adding follow-up ({followup_score}/10, threshold={FOLLOWUP_THRESHOLD})")
                    
                    await ws_manager.emit_agent_typing(followup_name, post_id, "start", context="reply", client_id=client_id, role=selected_role)
                    
                    followup_reply = await generate_discussion_reply(
                        selected_role,
                        conversation_context,
                        file_id,
                        original_context,
                        post_id=post_id,
                        agent_name=followup_name,
                        client_id=client_id,  # Pass client_id for isolation
                        image_file_path=image_file_path  # Pass image for multimodal
                    )
                    
                    await ws_manager.emit_agent_typing(followup_name, post_id, "end", context="reply", client_id=client_id, role=selected_role)
                    
                    if followup_reply:
                        followup_reply["agent_name"] = followup_name
                        replies.append(followup_reply)
                        conversation_context += f"\n\n{followup_name}: {followup_reply['content']}"
                        logger.info(f"{followup_name} (follow-up) added comment")
                        
                        if on_reply_generated:
                            try:
                                await on_reply_generated(followup_reply)
                            except Exception as cb_err:
                                logger.warning(f"Callback failed: {cb_err}")
                        
                        # Check for @mention in follow-up reply too (same validation + LLM analysis)
                        mentioned_role = _parse_agent_mention(followup_reply.get("content", ""))
                        if mentioned_role:
                            can_mention = (
                                mentioned_role != selected_role and  # Not self
                                mentioned_role not in responded_roles and  # Not already responded
                                mentioned_role in remaining_roles and  # Available
                                mention_count < MAX_MENTIONS_PER_DISCUSSION and  # Limit not reached
                                mentioned_role != last_responding_role  # No loops
                            )
                            if can_mention:
                                # LLM-based intent analysis
                                intent_result = await analyze_mention_intent(
                                    followup_reply.get("content", ""),
                                    mentioned_role
                                )
                                if intent_result["is_request"] and intent_result.get("confidence", 0) >= 0.6:
                                    pending_mention = mentioned_role
                                    mention_count += 1
                                    logger.info(f"Agent collaboration: {followup_name} → @{mentioned_role} ({mention_count}/{MAX_MENTIONS_PER_DISCUSSION})")
                                else:
                                    logger.info(f"@mention ignored: {intent_result['intent']} (not a request)")
                else:
                    logger.info(f"{followup_name} skipped follow-up ({followup_score}/10 < {FOLLOWUP_THRESHOLD})")
        
        # 7. Track responded role and update state
        responded_roles.add(selected_role)
        last_responding_role = selected_role
        
        # 8. Remove this role from candidates, move to next
        remaining_roles.remove(selected_role)
        role_round += 1
    
    logger.info(f"Discussion complete: {len(replies)} replies over {role_round - 1} role rounds")
    return replies


async def run_proactive_analysis(
    previous_discussion: Optional[str],
    file_id: str,
    file_path: Optional[str] = None,
    client_id: Optional[str] = None,
    direction: Optional[str] = None,
    user_goal: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run proactive scanner agent with context and direction
    
    Args:
        previous_discussion: Summary of previous discussion, or None for initial scan
        file_id: Dataset file ID
        file_path: Path to the dataset file (required for CSV loading)
        client_id: Client ID for session isolation
        direction: Specific exploration direction suggested by LLM
        user_goal: User's original analysis goal from their first POST
        
    Returns:
        Dict with agent response and metadata
    """
    from utils.logger import logger
    
    # Build context-aware message
    message = build_proactive_analysis_message(direction, previous_discussion, user_goal)
    if direction:
        logger.info(f"Running directed proactive analysis: {direction[:50]}...")
    elif previous_discussion:
        logger.info(f"Running proactive follow-up analysis for file_id={file_id}")
    elif user_goal:
        logger.info(f"Running goal-aligned initial scan for file_id={file_id}")
    else:
        logger.info(f"Running initial proactive scan for file_id={file_id}")
    
    result = await run_agent_analysis(
        message=message,
        file_path=file_path,
        file_id=file_id,
        agent=proactive_scanner_agent,
        ws_context="post",
        client_id=client_id,
    )
    
    logger.info(f"Proactive analysis complete: {len(result.get('content', ''))} chars")
    
    return result


# ==================== Auto-Tagging Functions ====================

# Valid tag types
VALID_TAGS = {"hypothesis", "evidence", "question", "todo", "insight"}

async def auto_tag_content(content: str, context: str = None, feed_context: List[dict] = None) -> List[str]:
    """
    Automatically classify content into tag categories using LLM.
    Uses full feed context for accurate classification.
    
    Args:
        content: Text content to classify
        context: Optional immediate conversation context
        feed_context: Optional list of all posts/replies for full context
        
    Returns:
        List of applicable tag identifiers
    """
    from utils.logger import logger
    from openai import AsyncOpenAI
    
    if not content or len(content.strip()) < 20:
        return []
    
    truncated_content = truncate_text(content, 220, preserve="head")
    
    # Build full feed context section
    feed_section = ""
    if feed_context and len(feed_context) > 0:
        feed_items = []
        for item in feed_context[-15:]:  # Last 15 items for context
            item_type = item.get("type", "post")
            author = item.get("author", "Unknown")
            item_content = truncate_text(item.get("content", ""), 45, preserve="head")
            existing_tags = item.get("tags", [])
            tag_str = f" [{', '.join(existing_tags)}]" if existing_tags else ""
            feed_items.append(f"[{item_type.upper()}]{tag_str} {author}: {item_content}...")
        
        feed_section = f"""
=== FULL CONVERSATION HISTORY ===
{chr(10).join(feed_items)}
=================================
"""
    elif context:
        feed_section = f"""
=== IMMEDIATE CONTEXT ===
        {truncate_text(context, 170, preserve="tail")}
=========================
"""
    
    prompt = build_semantic_tagging_prompt(feed_section, truncated_content)

    try:
        logger.debug(f"Auto-tagging with full context: {truncated_content[:50]}...")
        
        client = AsyncOpenAI()
        response = await client.chat.completions.create(
            model=settings.UTILITY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=50,
            temperature=0.1  # Low temperature for consistent classification
        )
        
        response_text = (response.choices[0].message.content or "").lower().strip()
        
        # Parse tags from response
        tags = []
        for tag in VALID_TAGS:
            if tag in response_text:
                tags.append(tag)
        
        # Limit to max 2 tags to avoid over-tagging
        tags = tags[:2]
        
        logger.info(f"Auto-tagged: {tags if tags else 'none'}")
        return tags
        
    except Exception as e:
        logger.error(f"Auto-tagging failed: {str(e)}")
        return []


def tag_content_sync(content: str) -> List[str]:
    """Synchronous wrapper for auto_tag_content"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If already in async context, schedule as task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, auto_tag_content(content))
                return future.result(timeout=10)
        else:
            return loop.run_until_complete(auto_tag_content(content))
    except Exception:
        return []


# ==================== Connection Inference ====================

# Valid connection types
VALID_CONNECTIONS = {"supports", "contradicts", "answers", "extends", "questions"}

async def infer_connections(
    content: str, 
    item_id: str,
    item_type: str,  # "post" or "reply"
    tags: List[str],
    previous_items: List[dict]
) -> List[dict]:
    """
    Infer connections between a new item and previous tagged items using LLM.
    
    Args:
        content: Content of the new item
        item_id: ID of the new item
        item_type: "post" or "reply"
        tags: Tags of the new item
        previous_items: List of previous tagged items with id, type, content, tags
        
    Returns:
        List of connections: [{"target_id": ..., "relation": ..., "confidence": ...}]
    """
    from utils.logger import logger
    from openai import AsyncOpenAI
    from config import settings
    
    if not content or not previous_items:
        logger.debug(f"infer_connections: early return - content={bool(content)}, previous_items={len(previous_items) if previous_items else 0}")
        return []
    
    # Include ALL previous items (prioritize tagged ones but include all)
    # Sort by: tagged items first, then by recency
    tagged_items = [item for item in previous_items if item.get("tags")]
    untagged_items = [item for item in previous_items if not item.get("tags")]
    
    # Combine: tagged first, then untagged
    relevant_items = tagged_items[-8:] + untagged_items[-4:]  # Max 12 items
    
    logger.debug(f"infer_connections: tagged={len(tagged_items)}, untagged={len(untagged_items)}, relevant={len(relevant_items)}")
    
    if not relevant_items:
        logger.debug("infer_connections: no relevant items, returning []")
        return []
    
    # Build items context
    items_context = ""
    for i, item in enumerate(relevant_items):
        item_tags = ", ".join(item.get("tags", []))
        item_content = truncate_text(item.get("content", ""), 45, preserve="head")
        items_context += f"{i+1}. [{item_tags}] (ID: {item.get('id')}): {item_content}...\n"
    
    prompt = build_semantic_connection_prompt(
        content=content,
        tags=tags,
        items_context=items_context,
        item_count=len(relevant_items),
    )

    try:
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        
        response = await client.chat.completions.create(
            model=settings.UTILITY_MODEL,
            messages=[
                {"role": "system", "content": SEMANTIC_CONNECTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_completion_tokens=300
        )
        
        result_text = (response.choices[0].message.content or "").strip()
        logger.debug(f"LLM raw response: {result_text[:200]}...")
        
        # Clean up response
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()
        
        import json
        raw_connections = json.loads(result_text)
        logger.debug(f"Parsed connections: {raw_connections}")
        
        # Convert to actual item IDs
        connections = []
        for conn in raw_connections:
            target_idx = conn.get("target", 0) - 1
            if 0 <= target_idx < len(relevant_items):
                target_item = relevant_items[target_idx]
                relation = conn.get("relation", "").lower()
                confidence = conn.get("confidence", 0.0)
                
                if relation in VALID_CONNECTIONS and confidence >= 0.4:
                    connections.append({
                        "target_id": target_item.get("id"),
                        "target_type": target_item.get("type", "post"),
                        "relation": relation,
                        "confidence": round(confidence, 2),
                        "target_summary": target_item.get("content", "")[:50]
                    })
        
        if connections:
            logger.info(f"Inferred {len(connections)} connections for {item_type} {item_id}")
            for conn in connections:
                logger.debug(f"- {conn['relation']} -> {conn['target_id']} ({conn['confidence']})")
        
        return connections
        
    except Exception as e:
        logger.error(f"Connection inference failed: {str(e)}")
        return []
