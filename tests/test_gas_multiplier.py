"""
The SDK has exactly one gas multiplier: TxManager.gas_adjustment, applied to
the estimate. An earlier revision also carried AlloraNetworkConfig.gas_adjustment
as the base of the retry ladder, and AlloraRPCClient.from_env fed BOTH from the
same GAS_ADJUSTMENT variable — so GAS_ADJUSTMENT=1.5 broadcast at 2.25x. These
tests pin the consolidated behaviour so a second multiplier cannot creep back.
"""
import logging

import pytest

from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.config import AlloraNetworkConfig, AlloraWalletConfig


def test_network_config_has_no_gas_multiplier():
    assert not hasattr(AlloraNetworkConfig.testnet(), "gas_adjustment")


def test_gas_adjustment_env_var_has_exactly_one_consumer(monkeypatch):
    reads: list[str] = []
    import os

    real_getenv = os.getenv

    def _spy(name, default=None):
        if name == "GAS_ADJUSTMENT":
            reads.append(name)
        return real_getenv(name, default)

    monkeypatch.setattr(os, "getenv", _spy)
    monkeypatch.setenv("GAS_ADJUSTMENT", "1.5")
    monkeypatch.setenv("CHAIN_ID", "allora-testnet-1")
    monkeypatch.setenv("RPC_ENDPOINT", "grpc+https://example:443")
    monkeypatch.setenv("FEE_DENOM", "uallo")
    monkeypatch.setenv("FEE_MIN_GAS_PRICE", "10")
    monkeypatch.setenv("WEBSOCKET_ENDPOINT", "wss://example/websocket")
    monkeypatch.setenv("FAUCET_URL", "https://example/faucet")

    AlloraNetworkConfig.from_env()
    assert reads == [], "AlloraNetworkConfig must not consume GAS_ADJUSTMENT"

    # The real consumer: from_env must read it once and hand it to exactly one
    # place. A second reader here is the 2.25x regression coming back.
    captured = {}
    monkeypatch.setattr(AlloraRPCClient, "__init__", lambda self, **kw: captured.update(kw))
    AlloraRPCClient.from_env(
        network=AlloraNetworkConfig.testnet(),
        wallet=AlloraWalletConfig(mnemonic="abandon " * 11 + "about"),
    )
    assert reads == ["GAS_ADJUSTMENT"], f"GAS_ADJUSTMENT read {len(reads)} times, expected once"
    assert captured["gas_adjustment"] == 1.5
    assert not hasattr(captured["network"], "gas_adjustment")


@pytest.mark.asyncio
async def test_first_attempt_broadcasts_exactly_the_adjusted_estimate():
    from unittest.mock import AsyncMock, Mock

    from allora_sdk.rpc_client.tx_manager import TxManager

    config = AlloraNetworkConfig.testnet()
    config.use_dynamic_gas_price = False
    config.congestion_aware_fees = False

    manager = TxManager(
        wallet=Mock(), tx_client=Mock(), auth_client=Mock(), bank_client=Mock(),
        feemarket_client=Mock(), config=config, gas_adjustment=1.5,
    )
    manager._pre_flight_checks = AsyncMock(return_value=None)

    captured: list[float] = []

    async def _broadcast(type_url, msgs, gas_limit, fee_multiplier, gas_multiplier, *a, **kw):
        captured.append(gas_multiplier)
        raise RuntimeError("stop after the first attempt")

    manager._build_and_broadcast = _broadcast

    from datetime import timedelta

    from allora_sdk.rpc_client.tx_manager import FeeTier, PendingTx

    pending = PendingTx(
        manager=manager, parent_tx_id=0, type_url="/cosmos.bank.v1beta1.MsgSend",
        msgs=[Mock()], fee_tier=FeeTier.ECO, max_retries=0, timeout=timedelta(seconds=10),
    )
    await manager._attempt_submissions(pending, gas_limit=150000)
    with pytest.raises(Exception):
        await pending

    # 1.0, not gas_adjustment: the estimate already carries the 1.5x, and
    # multiplying again is the double-application this consolidation removed.
    assert captured == [1.0]


def test_sub_unit_gas_adjustment_warns(caplog):
    from unittest.mock import Mock

    from allora_sdk.rpc_client.tx_manager import TxManager

    config = AlloraNetworkConfig.testnet()
    with caplog.at_level(logging.WARNING, logger="allora_sdk"):
        TxManager(
            wallet=Mock(), tx_client=Mock(), auth_client=Mock(), bank_client=Mock(),
            feemarket_client=Mock(), config=config, gas_adjustment=0.5,
        )
    assert any("below 1.0" in r.message for r in caplog.records)
