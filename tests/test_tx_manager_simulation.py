from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from cosmpy.crypto.address import Address
from cosmpy.crypto.keypairs import PrivateKey

from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.tx_manager import (
    FeeTier,
    InsufficientBalanceError,
    TxError,
    TxManager,
)

# A valid allo1 bech32 address (cosmpy's Address validates the checksum and HRP).
VALID_GRANTER = str(Address(PrivateKey().public_key, "allo"))


def _make_manager(fee_granter: str | None = None) -> TxManager:
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
        fee_granter=fee_granter,
    )


def test_fee_granter_is_stored() -> None:
    assert _make_manager()._fee_granter is None
    assert _make_manager()._granter_address is None

    mgr = _make_manager(fee_granter=VALID_GRANTER)
    assert mgr._fee_granter == VALID_GRANTER
    # The granter is parsed once at construction and reused on every broadcast.
    assert mgr._granter_address == Address(VALID_GRANTER)


@pytest.mark.asyncio
async def test_pre_flight_skipped_with_fee_granter() -> None:
    # A feegrant wallet holds no ALLO; the granter pays fees. Pre-flight must not query or
    # reject on balance, otherwise a zero-balance signing wallet is wrongly blocked (ENGN-8456).
    mgr = _make_manager(fee_granter=VALID_GRANTER)
    mgr.auth_client.account = AsyncMock(return_value=Mock())
    zero_balance = Mock()
    zero_balance.balance = Mock(amount="0")
    mgr.bank_client.balance = AsyncMock(return_value=zero_balance)

    await mgr._pre_flight_checks()  # must not raise

    mgr.bank_client.balance.assert_not_called()


@pytest.mark.asyncio
async def test_pre_flight_rejects_low_balance_without_granter() -> None:
    # Without a granter the signing wallet pays its own fees, so an empty balance must still fail.
    mgr = _make_manager()
    mgr.auth_client.account = AsyncMock(return_value=Mock())
    zero_balance = Mock()
    zero_balance.balance = Mock(amount="0")
    mgr.bank_client.balance = AsyncMock(return_value=zero_balance)

    with pytest.raises(InsufficientBalanceError):
        await mgr._pre_flight_checks()


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
        type_url="/emissions.v9.InsertWorkerPayloadRequest",
        msgs=[Mock()],
        fee_tier=FeeTier.STANDARD,
    )

    # Current behavior: simulation errors are swallowed and gas falls back to defaults.
    assert pending.type_url == "/emissions.v9.InsertWorkerPayloadRequest"
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
        type_url="/emissions.v9.InsertWorkerPayloadRequest",
        msgs=[Mock()],
        fee_tier=FeeTier.PRIORITY,
    )

    manager._attempt_submissions.assert_called_once()
    args, kwargs = manager._attempt_submissions.call_args
    assert args[1] == 345678
    assert kwargs["account_seq"] is None

    for task in scheduled:
        await task


def test_signing_pool_size_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from allora_sdk.rpc_client._executors import DEFAULT_SIGNING_POOL_SIZE, _signing_pool_size

    monkeypatch.delenv("ALLORA_SIGNING_POOL_SIZE", raising=False)
    assert _signing_pool_size() == DEFAULT_SIGNING_POOL_SIZE


def test_signing_pool_size_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    from allora_sdk.rpc_client._executors import _signing_pool_size

    monkeypatch.setenv("ALLORA_SIGNING_POOL_SIZE", "16")
    assert _signing_pool_size() == 16


def test_signing_pool_size_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from allora_sdk.rpc_client._executors import DEFAULT_SIGNING_POOL_SIZE, _signing_pool_size

    monkeypatch.setenv("ALLORA_SIGNING_POOL_SIZE", "not-a-number")
    assert _signing_pool_size() == DEFAULT_SIGNING_POOL_SIZE
    monkeypatch.setenv("ALLORA_SIGNING_POOL_SIZE", "0")
    assert _signing_pool_size() == DEFAULT_SIGNING_POOL_SIZE
