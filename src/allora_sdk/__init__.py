"""Allora Network Python SDK.

Heavy RPC/worker imports are lazy so lightweight clients (e.g. Forge HTTP API)
can be used without generated protobuf packages present.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AlloraWorker",
    "AlloraRPCClient",
    "AlloraAPIClient",
    "AlloraNetworkConfig",
    "AlloraWalletConfig",
    "FeeTier",
    "TxManager",
    "MaxFeesExceededError",
    "setup_sdk_logging",
    "LocalWallet",
    "PrivateKey",
    "get_default_loss_fn",
    "is_supported_loss_method",
    "SUPPORTED_LOSS_METHODS",
    "UnsupportedLossMethodError",
    "get_block_time",
    "get_network_inference",
    "make_reputer_function",
    "RunContext",
    "ValueBundle",
]

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "AlloraWorker": (".worker", "AlloraWorker"),
    "AlloraRPCClient": (".rpc_client", "AlloraRPCClient"),
    "AlloraNetworkConfig": (".rpc_client", "AlloraNetworkConfig"),
    "AlloraWalletConfig": (".rpc_client", "AlloraWalletConfig"),
    "TxManager": (".rpc_client", "TxManager"),
    "FeeTier": (".rpc_client", "FeeTier"),
    "MaxFeesExceededError": (".rpc_client", "MaxFeesExceededError"),
    "ValueBundle": (".rpc_client.protos.emissions.v3", "ValueBundle"),
    "AlloraAPIClient": (".api_client", "AlloraAPIClient"),
    "setup_sdk_logging": (".logging_config", "setup_sdk_logging"),
    "get_default_loss_fn": (".loss_methods", "get_default_loss_fn"),
    "is_supported_loss_method": (".loss_methods", "is_supported_loss_method"),
    "SUPPORTED_LOSS_METHODS": (".loss_methods", "SUPPORTED_LOSS_METHODS"),
    "UnsupportedLossMethodError": (".loss_methods", "UnsupportedLossMethodError"),
    "get_block_time": (".worker.utils", "get_block_time"),
    "get_network_inference": (".worker.utils", "get_network_inference"),
    "make_reputer_function": (".worker.utils", "make_reputer_function"),
    "RunContext": (".worker.context", "RunContext"),
    "LocalWallet": ("cosmpy.aerial.wallet", "LocalWallet"),
    "PrivateKey": ("cosmpy.aerial.wallet", "PrivateKey"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    if module_name.startswith("."):
        import importlib

        module = importlib.import_module(module_name, __name__)
    else:
        import importlib

        module = importlib.import_module(module_name)
    value = getattr(module, attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(__all__))
