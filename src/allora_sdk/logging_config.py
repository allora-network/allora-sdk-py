"""
Centralized logging configuration for the Allora SDK.
"""

import logging
import sys

_LOGGING_CONFIGURED = False

class ThreeCharLevelFormatter(logging.Formatter):
    """Custom formatter that uses 3-character log level names with optional colors."""

    LEVEL_MAPPING = {
        'DEBUG': 'DBG',
        'INFO': 'INF',
        'WARNING': 'WRN',
        'ERROR': 'ERR',
        'CRITICAL': 'CRT',
    }

    # ANSI color codes
    COLORS = {
        'DBG': '\033[36m',      # Cyan
        'INF': '\033[32m',      # Green
        'WRN': '\033[33m',      # Yellow
        'ERR': '\033[31m',      # Red
        'CRT': '\033[35m',      # Magenta
        'RESET': '\033[0m',     # Reset
    }

    def __init__(self, fmt: str, use_color: bool = True):
        super().__init__(fmt)
        self.use_color = use_color and sys.stdout.isatty()

    def format(self, record: logging.LogRecord) -> str:
        levelname = self.LEVEL_MAPPING.get(record.levelname, record.levelname[:3])

        if self.use_color:
            color = self.COLORS.get(levelname, '')
            reset = self.COLORS['RESET']
            record.levelname = f"{color}{levelname}{reset}"
        else:
            record.levelname = levelname

        return super().format(record)

def setup_sdk_logging(debug: bool = False, force: bool = False, use_color: bool = True):
    """
    Configure logging for the entire Allora SDK.

    Args:
        debug: If True, set DEBUG level, otherwise INFO level
        force: If True, reconfigure even if already configured
        use_color: If True, use colored output (auto-disabled for non-TTY)
    """
    global _LOGGING_CONFIGURED

    if _LOGGING_CONFIGURED and not force:
        return

    sdk_level = logging.DEBUG if debug else logging.INFO

    # Create custom formatter with 3-character level names and optional colors
    formatter = ThreeCharLevelFormatter(
        fmt='%(asctime)s %(levelname)s %(message)s',
        use_color=use_color
    )

    # Configure handler with custom formatter
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Configure root logger with WARNING level to suppress third-party debug logs
    # The handler has no level filter, so it will output any logs that pass through
    logging.basicConfig(
        level=logging.WARNING,
        handlers=[handler],
        force=True
    )

    # Configure all SDK loggers explicitly with the requested level
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
        logger.setLevel(sdk_level)
        logger.propagate = True
    
    _LOGGING_CONFIGURED = True

def is_configured() -> bool:
    """Check if SDK logging has been configured."""
    return _LOGGING_CONFIGURED

