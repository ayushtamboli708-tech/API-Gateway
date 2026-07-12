import logging
import json
from datetime import datetime, timezone

# Initialize the logger
logger = logging.getLogger("gateway")
logger.setLevel(logging.INFO)

# Prevent duplicate logs if this module is imported multiple times
if not logger.handlers:
    handler = logging.StreamHandler()
    logger.addHandler(handler)

def log_event(event_type: str, **fields):
    """
    Outputs a structured JSON log entry for monitoring and observability.
    """
    log_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **fields
    }
    
    logger.info(json.dumps(log_data))