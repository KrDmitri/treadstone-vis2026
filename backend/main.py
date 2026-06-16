"""
Treadstone Backend API
SNS-style multi-agent collaboration system for data analysis
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings
from routers import feed, files, agent, analysis, agents_mgmt
from services.websocket_manager import ws_manager
from utils.logger import logger

# ==================== FastAPI App ====================

app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description=settings.API_DESCRIPTION
)

# CORS Middleware (for HTTP requests)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for uploads (images, etc.)
settings.ensure_upload_dir()
app.mount("/uploads", StaticFiles(directory=str(settings.UPLOAD_DIR)), name="uploads")

# Include routers
app.include_router(feed.router)
app.include_router(files.router)
app.include_router(agent.router)
app.include_router(analysis.router)
app.include_router(agents_mgmt.router)

# ==================== WebSocket Endpoint ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, client_id: str = None):
    """
    WebSocket endpoint for real-time updates.
    
    Connect with client_id for isolated messaging:
    ws://localhost:8000/ws?client_id=client_123456_abc
    """
    # Accept the connection with client_id for isolation
    await ws_manager.connect(websocket, client_id)
    
    try:
        while True:
            # Keep connection alive, receive any client messages (ping/pong)
            data = await websocket.receive_text()
            # Echo back for ping/pong or handle client messages if needed
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, client_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket, client_id)

# ==================== Root Endpoint ====================

@app.get("/")
async def root():
    """Check API status"""
    return {
        "message": settings.API_TITLE,
        "version": settings.API_VERSION,
        "status": "running"
    }

# ==================== Legacy Endpoints (Deprecated) ====================

@app.get("/api/assistants")
async def get_assistants():
    """
    Legacy endpoint for backwards compatibility
    Returns information about available agents
    """
    from agents_sdk import analyst_agent, visualizer_agent, insight_agent, summary_feed_agent, proactive_scanner_agent
    
    assistants = {
        "statistics": {"name": analyst_agent.name, "role": "Statistical Analyst"},
        "visualization": {"name": visualizer_agent.name, "role": "Visualization Expert"},
        "insight": {"name": insight_agent.name, "role": "Intelligence"},
        "summary": {"name": summary_feed_agent.name, "role": "Synthesis"},
        "scanner": {"name": proactive_scanner_agent.name, "role": "Data Scout"}
    }
    
    return {"assistants": assistants}

# ==================== Server Entry Point ====================

if __name__ == "__main__":
    import uvicorn
    import logging
    
    print(f"\nStarting {settings.API_TITLE} v{settings.API_VERSION}")
    print(f"Upload directory: {settings.UPLOAD_DIR}")
    print(f"Allowed file types: {settings.ALLOWED_EXTENSIONS}")
    print(f"WebSocket endpoint: ws://localhost:{settings.PORT}/ws\n")
    
    # Configure custom access log filter to hide polling endpoints
    class PollEndpointFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            # Hide repetitive GET requests for polling endpoints
            message = record.getMessage()
            if 'GET /api/feed' in message or 'GET /api/analysis/summary' in message:
                return False
            return True
    
    # Apply filter to uvicorn access logger
    logging.getLogger("uvicorn.access").addFilter(PollEndpointFilter())
    
    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
        log_level="info"
    )
