"""Public exports for RPC client package with lazy loading."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import AlloraRPCClient
    from .config import AlloraNetworkConfig, AlloraWalletConfig
    from .tx_manager import FeeTier, TxManager


__all__ = [
    "AlloraRPCClient",
    "AlloraNetworkConfig",
    "AlloraWalletConfig",
    "TxManager",
    "FeeTier",
]


def __getattr__(name: str) -> Any:
    if name == "AlloraRPCClient":
        return import_module(".client", __name__).AlloraRPCClient
    if name in {"AlloraNetworkConfig", "AlloraWalletConfig"}:
        module = import_module(".config", __name__)
        return getattr(module, name)
    if name in {"TxManager", "FeeTier"}:
        module = import_module(".tx_manager", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")