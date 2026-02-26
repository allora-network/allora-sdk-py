"""Top-level public exports for the Allora SDK.

This module uses lazy attribute loading so importing lightweight surfaces
(`allora_sdk.api_client`) does not require heavyweight RPC/protobuf modules.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cosmpy.aerial.wallet import LocalWallet, PrivateKey

    from .api_client import AlloraAPIClient
    from .logging_config import setup_sdk_logging
    from .rpc_client import AlloraNetworkConfig, AlloraRPCClient, AlloraWalletConfig, FeeTier, TxManager
    from .worker import AlloraWorker

__all__ = [
    "AlloraWorker",
    "AlloraRPCClient",
    "AlloraAPIClient",
    "AlloraNetworkConfig",
    "AlloraWalletConfig",
    "FeeTier",
    "TxManager",
    "setup_sdk_logging",
    "LocalWallet",
    "PrivateKey",
]


def __getattr__(name: str) -> Any:
    if name == "AlloraWorker":
        value = import_module(".worker", __name__).AlloraWorker
        globals()[name] = value
        return value
    if name in {"AlloraRPCClient", "AlloraNetworkConfig", "AlloraWalletConfig", "TxManager", "FeeTier"}:
        module = import_module(".rpc_client", __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name == "AlloraAPIClient":
        value = import_module(".api_client", __name__).AlloraAPIClient
        globals()[name] = value
        return value
    if name == "setup_sdk_logging":
        value = import_module(".logging_config", __name__).setup_sdk_logging
        globals()[name] = value
        return value
    if name in {"LocalWallet", "PrivateKey"}:
        module = import_module("cosmpy.aerial.wallet")
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
