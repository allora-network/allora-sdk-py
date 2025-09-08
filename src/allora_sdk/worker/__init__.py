"""
Allora Worker Module

ML-friendly async generator interface for blockchain prediction submission.
Provides automatic WebSocket subscription management, environment-aware signal handling,
and graceful resource cleanup for submitting predictions to Allora network topics.
"""

from .worker import AlloraWorker, PredictFnResultType

__all__ = [
    "AlloraWorker",
    "PredictFnResultType",
]