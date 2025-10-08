"""
Centralized logging configuration for the Allora SDK.
"""

import logging
import sys

_LOGGING_CONFIGURED = False

def setup_sdk_logging(debug: bool = False, force: bool = False):
    """
    Configure logging for the entire Allora SDK.
    
    Args:
        debug: If True, set DEBUG level, otherwise INFO level
        force: If True, reconfigure even if already configured
    """
    global _LOGGING_CONFIGURED
    
    if _LOGGING_CONFIGURED and not force:
        return
        
    level = logging.DEBUG if debug else logging.INFO
    
    # Force reconfiguration (important for Colab/Jupyter)
    logging.basicConfig(
        level=level,
        format='%(asctime)s %(levelname)s %(message)s',
        force=True,
        stream=sys.stdout  # Ensure output goes to stdout for notebook visibility
    )
    
    # Configure all SDK loggers explicitly
    sdk_loggers = [
        'allora_sdk',
        'allora_sdk.worker',
        'allora_sdk.worker.worker', 
        'allora_sdk.rpc_client',
        'allora_sdk.api_client_v2',
        'allora_sdk.protobuf_client',
    ]
    
    for logger_name in sdk_loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.propagate = True
    
    _LOGGING_CONFIGURED = True

def is_configured() -> bool:
    """Check if SDK logging has been configured."""
    return _LOGGING_CONFIGURED