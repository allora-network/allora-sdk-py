from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from grpclib.const import Status
from grpclib.exceptions import GRPCError

from cosmpy.aerial.wallet import Wallet

from allora_sdk.rpc_client.chain_errors import (
    EMISSIONS_CODESPACE,
    EMISSIONS_ERR_NOT_PERMITTED_TO_SUBMIT_FORECASTER_PAYLOAD,
    EMISSIONS_ERR_UNFULFILLED_NONCE_NOT_FOUND,
)
from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.tx_manager import (
    FeeTier,
    SimulationUnavailableError,
    TxError,
    TxManager,
)


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
async def test_submit_transaction_raises_when_simulation_raises_tx_error() -> None:
    manager = _make_manager()
    manager.simulate_transaction = AsyncMock(
        side_effect=TxError(codespace="emissions", code=13, message="unauthorized")
    )
    manager._attempt_submissions = AsyncMock(return_value=None)

    with pytest.raises(TxError) as exc:
        await manager.submit_transaction(
            type_url="/emissions.v10.InsertWorkerPayloadRequest",
            msgs=[Mock()],
            fee_tier=FeeTier.STANDARD,
        )

    assert exc.value.code == 13
    manager._attempt_submissions.assert_not_called()


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


def test_simulation_unavailable_status_is_not_tx_error() -> None:
    manager = _make_manager()
    err = GRPCError(Status.UNAVAILABLE, "connection reset", None)
    mapped = manager._exception_from_simulation_error(err)
    assert isinstance(mapped, SimulationUnavailableError)
    assert not isinstance(mapped, TxError)


def test_simulation_abci_message_preserves_emissions_code() -> None:
    manager = _make_manager()
    err = GRPCError(
        Status.UNKNOWN,
        "failed to execute message: codespace emissions code 104: not permitted",
        None,
    )
    mapped = manager._exception_from_simulation_error(err)
    assert isinstance(mapped, TxError)
    assert mapped.codespace == EMISSIONS_CODESPACE
    assert mapped.code == EMISSIONS_ERR_NOT_PERMITTED_TO_SUBMIT_FORECASTER_PAYLOAD


def test_simulation_abci_message_unfulfilled_nonce() -> None:
    manager = _make_manager()
    err = GRPCError(
        Status.UNKNOWN,
        "failed to execute message; message index: 0: "
        "codespace emissions code 75: unfulfilled nonce not found",
        None,
    )
    mapped = manager._exception_from_simulation_error(err)
    assert isinstance(mapped, TxError)
    assert mapped.code == EMISSIONS_ERR_UNFULFILLED_NONCE_NOT_FOUND


@pytest.mark.asyncio
async def test_submit_transaction_falls_back_on_simulation_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _make_manager()
    manager.simulate_transaction = AsyncMock(
        side_effect=SimulationUnavailableError("UNAVAILABLE")
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
    assert pending.type_url == "/emissions.v10.InsertWorkerPayloadRequest"
    args, _ = manager._attempt_submissions.call_args
    assert args[1] is None  # static gas fallback

    for task in scheduled:
        await task
