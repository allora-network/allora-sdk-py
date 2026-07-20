from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from cosmpy.aerial.wallet import Wallet

from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.tx_manager import FeeTier, TxError, TxManager


class _StubWallet(Wallet):
    """Minimal `Wallet` ABC subclass standing in for a custodial/remote signer."""

    def __init__(self) -> None:
        # Wallet exposes `data` as a read-only property, so UserString.__init__
        # (which assigns self.data) must be bypassed.
        pass

    def address(self):
        return "allo1stub"

    def public_key(self):
        return Mock()

    def signer(self):
        return Mock()


def test_tx_manager_accepts_wallet_abc_subclass() -> None:
    """An injected signer only satisfying the `Wallet` ABC must construct cleanly."""
    wallet = _StubWallet()

    manager = TxManager(
        wallet=wallet,
        tx_client=Mock(),
        auth_client=Mock(),
        bank_client=Mock(),
        feemarket_client=Mock(),
        config=AlloraNetworkConfig.testnet(),
    )

    assert manager.wallet is wallet
    assert isinstance(manager.wallet, Wallet)


def _make_manager() -> TxManager:
    wallet = Mock()
    wallet.address.return_value = "allo1sender"
    wallet.public_key.return_value = Mock()

    tx_client = Mock()
    auth_client = Mock()
    bank_client = Mock()
    feemarket_client = Mock()
    config = AlloraNetworkConfig.testnet()

    return TxManager(
        wallet=wallet,
        tx_client=tx_client,
        auth_client=auth_client,
        bank_client=bank_client,
        feemarket_client=feemarket_client,
        config=config,
    )


@pytest.mark.asyncio
async def test_submit_transaction_falls_back_when_simulation_raises_tx_error(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _make_manager()
    manager.simulate_transaction = AsyncMock(
        side_effect=TxError(codespace="emissions", code=13, message="unauthorized")
    )
    manager._attempt_submissions = AsyncMock(return_value=None)

    scheduled: list[asyncio.Task] = []

    def _capture_task(coro):
        task = asyncio.get_running_loop().create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", _capture_task)

    pending = await manager.submit_transaction(
        type_url="/emissions.v10.InsertWorkerPayloadRequest",
        msgs=[Mock()],
        fee_tier=FeeTier.STANDARD,
    )

    # Current behavior: simulation errors are swallowed and gas falls back to defaults.
    assert pending.type_url == "/emissions.v10.InsertWorkerPayloadRequest"
    manager._attempt_submissions.assert_called_once()
    args, kwargs = manager._attempt_submissions.call_args
    assert args[1] is None  # gas_limit
    assert kwargs["account_seq"] is None

    for task in scheduled:
        await task


@pytest.mark.asyncio
async def test_submit_transaction_uses_simulated_gas_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _make_manager()
    manager.simulate_transaction = AsyncMock(return_value=345678)
    manager._attempt_submissions = AsyncMock(return_value=None)

    scheduled: list[asyncio.Task] = []

    def _capture_task(coro):
        task = asyncio.get_running_loop().create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", _capture_task)

    await manager.submit_transaction(
        type_url="/emissions.v10.InsertWorkerPayloadRequest",
        msgs=[Mock()],
        fee_tier=FeeTier.PRIORITY,
    )

    manager._attempt_submissions.assert_called_once()
    args, kwargs = manager._attempt_submissions.call_args
    assert args[1] == 345678
    assert kwargs["account_seq"] is None

    for task in scheduled:
        await task
