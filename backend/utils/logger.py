"""
Logging configuration for Treadstone backend
"""
import logging
import sys
from datetime import datetime

logger = logging.getLogger("treadstone")
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)

class ColoredFormatter(logging.Formatter):
    """Console formatter with level colors."""
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    
    RESET = '\033[0m'
    
    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        message = super().format(record)
        
        return f"{color}[{timestamp}] [{record.levelname}] {record.name}: {message}{self.RESET}"

formatter = ColoredFormatter('%(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

logger.propagate = False
