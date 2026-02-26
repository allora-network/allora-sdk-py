"""
Allora Worker Module

ML-friendly async generator interface for blockchain prediction submission.
Provides automatic WebSocket subscription management, environment-aware signal handling,
and graceful resource cleanup for submitting predictions to Allora network topics.
"""

from .worker import AlloraWorker
from .reputer import LossFn
from allora_sdk.loss_methods.squared_error import squared_error_loss
from .autostake import (
    AutoStakeConfig,
    AutoStakeTargetType,
    InvalidAutoStakeConfigError,
)
from .inferer import SanityCheckConfig

# Backward compatibility alias
default_squared_error_loss = squared_error_loss

__all__ = [
    "AlloraWorker",
    "LossFn",
    "default_squared_error_loss",
    "AutoStakeConfig",
    "AutoStakeTargetType",
    "InvalidAutoStakeConfigError",
    "SanityCheckConfig",
]