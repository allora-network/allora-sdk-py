"""
Allora Worker Module

ML-friendly async generator interface for blockchain prediction submission.
Provides automatic WebSocket subscription management, environment-aware signal handling,
and graceful resource cleanup for submitting predictions to Allora network topics.
"""

from .worker import AlloraWorker
from allora_sdk.loss_methods.defaults import LossFn
from allora_sdk.loss_methods.squared_error import squared_error_loss
from .autostake import (
    AutoStakeConfig,
    AutoStakeTargetType,
    InvalidAutoStakeConfigError,
)
from .inferer import SanityCheckConfig
from .runner_config import WorkerRunnerConfig
from .function_registry import FunctionRegistry
from .worker_manager import WorkerManager

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
    "WorkerRunnerConfig",
    "FunctionRegistry",
    "WorkerManager",
]