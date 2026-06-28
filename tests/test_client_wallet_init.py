"""Unit tests for AlloraRPCClient._initialize_wallet credential resolution."""

import pytest

from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.config import AlloraWalletConfig


def test_initialize_wallet_rejects_deferred_forge_config():
    # A forge_api_key-only config defers provisioning to a worker topic; AlloraRPCClient cannot
    # supply one, so it must fail loudly rather than leave self.wallet=None (no tx_manager).
    # Build a bare instance and exercise the guard directly, avoiding network/LedgerClient setup.
    client = AlloraRPCClient.__new__(AlloraRPCClient)
    cfg = AlloraWalletConfig(forge_api_key="forge_sk_test", forge_backend_url="https://forge.invalid")
    with pytest.raises(ValueError, match="forge_api_key"):
        client._initialize_wallet(cfg)
