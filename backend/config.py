"""
Configuration management for Treadstone backend
Supports both development and production environments
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Settings:
    """Application settings with environment variable support"""
    
    # API Settings
    API_TITLE = "Treadstone API (OpenAI Agents SDK)"
    API_VERSION = "0.3.0"
    API_DESCRIPTION = "SNS-style multi-agent collaboration system for data analysis"
    
    # Environment
    ENV = os.getenv("ENV", "development")  # development, production
    DEBUG = os.getenv("DEBUG", "true").lower() == "true"
    
    # Server Settings
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "9000"))
    WORKERS = int(os.getenv("WORKERS", "1"))  # For gunicorn
    
    # OpenAI Settings
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    
    # File Storage Settings
    BASE_DIR = Path(__file__).parent
    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))
    ALLOWED_EXTENSIONS = {".csv", ".txt", ".jpg", ".jpeg", ".png", ".gif", ".webp"}
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(50 * 1024 * 1024)))  # 50MB
    
    # CORS Settings - Allow all origins in production for flexibility
    # You can restrict this by setting CORS_ORIGINS environment variable
    _cors_env = os.getenv("CORS_ORIGINS", "")
    CORS_ORIGINS = _cors_env.split(",") if _cors_env else ["*"]
    
    # Agent Configuration - Centralized Model Settings
    # Change these to update all agents at once
    DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-5.2")  # Main model for all agents
    UTILITY_MODEL = os.getenv("UTILITY_MODEL", "gpt-4o-mini")  # Fast/cheap for simple tasks (classification, etc.)
    
    AGENT_CONFIGS = {
        'scanner': {'model': os.getenv("SCANNER_MODEL", DEFAULT_MODEL)},
        'statistics': {'model': os.getenv("STATISTICS_MODEL", DEFAULT_MODEL)},
        'visualization': {'model': os.getenv("VISUALIZATION_MODEL", DEFAULT_MODEL)},
        'insight': {'model': os.getenv("INSIGHT_MODEL", DEFAULT_MODEL)},
        'summary': {'model': os.getenv("SUMMARY_MODEL", DEFAULT_MODEL)}
    }
    
    # Session Settings
    SESSION_DB_PATH = os.getenv("SESSION_DB_PATH", "sessions.db")
    
    @classmethod
    def ensure_upload_dir(cls):
        """Ensure upload directory exists"""
        cls.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def get_agent_model(cls, agent_key: str) -> str:
        """Get model for a specific agent"""
        return cls.AGENT_CONFIGS.get(agent_key, {}).get('model', cls.DEFAULT_MODEL)
    
    @classmethod
    def set_agent_model(cls, agent_key: str, model: str):
        """Set model for a specific agent"""
        if agent_key not in cls.AGENT_CONFIGS:
            cls.AGENT_CONFIGS[agent_key] = {}
        cls.AGENT_CONFIGS[agent_key]['model'] = model
    
    @classmethod
    def get_utility_model(cls) -> str:
        """Get utility model for simple/fast tasks (classification, tagging, etc.)"""
        return cls.UTILITY_MODEL
    
    @classmethod
    def is_production(cls) -> bool:
        """Check if running in production mode"""
        return cls.ENV == "production"
    
    @classmethod
    def get_log_level(cls) -> str:
        """Get appropriate log level for environment"""
        return "warning" if cls.is_production() else "info"

settings = Settings()
