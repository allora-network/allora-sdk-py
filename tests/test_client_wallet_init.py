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


def test_initialize_wallet_owns_sdk_built_wallet():
    # A wallet the SDK built (cfg._sdk_owned=True, e.g. from_env's RemoteWallet or a worker
    # factory) is client-owned, so AlloraRPCClient.close() releases its resources.
    from cosmpy.aerial.wallet import LocalWallet
    from cosmpy.crypto.keypairs import PrivateKey

    cfg = AlloraWalletConfig(wallet=LocalWallet(PrivateKey(), prefix="allo"))
    cfg._sdk_owned = True
    client = AlloraRPCClient.__new__(AlloraRPCClient)
    client._initialize_wallet(cfg)
    assert client._owns_wallet is True


def test_initialize_wallet_does_not_own_caller_supplied_wallet():
    # A caller-supplied pre-built wallet (default _sdk_owned=False) is left for the caller to
    # manage and may be shared across clients, so the client must not close it.
    from cosmpy.aerial.wallet import LocalWallet
    from cosmpy.crypto.keypairs import PrivateKey

    cfg = AlloraWalletConfig(wallet=LocalWallet(PrivateKey(), prefix="allo"))
    client = AlloraRPCClient.__new__(AlloraRPCClient)
    client._initialize_wallet(cfg)
    assert client._owns_wallet is False
