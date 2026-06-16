"""Helpers for routing suggested follow-up cards to core agents."""
from typing import Optional


VALID_TARGET_AGENT_ROLES = {"statistics", "visualization", "insight", "scanner", "summary"}


def normalize_target_agent_role(role: Optional[str]) -> Optional[str]:
    """Normalize hidden card routing metadata into a core agent role."""
    if not role:
        return None

    normalized = role.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "statistical": "statistics",
        "statistical_analyst": "statistics",
        "analyst": "statistics",
        "stats": "statistics",
        "stat": "statistics",
        "visualizer": "visualization",
        "visualization_expert": "visualization",
        "viz": "visualization",
        "chart": "visualization",
        "intelligence": "insight",
        "intelligence_agent": "insight",
        "insights": "insight",
        "summary_agent": "summary",
        "summarizer": "summary",
        "data_scout": "scanner",
        "scout": "scanner",
        "scanner_agent": "scanner",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in VALID_TARGET_AGENT_ROLES else None


def infer_next_step_target_agent_role(question: str) -> str:
    """Choose the most natural core agent for a suggested follow-up question."""
    q = (question or "").lower()

    visualization_terms = (
        "visualize", "visualise", "chart", "plot", "graph", "histogram",
        "scatter", "bar chart", "line chart", "heatmap", "map ",
    )
    summary_terms = (
        "summarize", "summarise", "summary", "synthesize", "synthesise",
        "recap", "overview", "takeaway", "key findings",
    )
    scanner_terms = (
        "explore", "discover", "scan", "hidden", "unexpected", "surprising",
        "anomaly", "anomalies", "outlier", "outliers", "new pattern",
    )
    insight_terms = (
        "why", "interpret", "explain", "meaning", "implication", "implications",
        "recommend", "strategy", "context", "important", "matter",
    )
    statistics_terms = (
        "mean", "median", "average", "count", "sum", "total", "rate",
        "percentage", "percent", "proportion", "distribution", "variance",
        "correlation", "compare", "comparison", "rank", "top", "bottom",
        "trend", "delta", "change", "contribution", "frequency",
    )

    if any(term in q for term in visualization_terms):
        return "visualization"
    if any(term in q for term in summary_terms):
        return "summary"
    if any(term in q for term in scanner_terms):
        return "scanner"
    if any(term in q for term in insight_terms):
        return "insight"
    if any(term in q for term in statistics_terms):
        return "statistics"

    return "statistics"
