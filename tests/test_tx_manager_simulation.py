from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import grpc
import pytest

from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.tx_manager import (
    AccountSequenceMismatchError,
    FeeTier,
    GasSimulationUnavailableError,
    InsufficientFeesError,
    OutOfGasError,
    TxError,
    TxManager,
)


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


class _FakeRpcError(grpc.RpcError):
    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


@pytest.mark.asyncio
async def test_submit_transaction_does_not_broadcast_when_simulation_rejects_tx() -> None:
    manager = _make_manager()
    manager.simulate_transaction = AsyncMock(
        side_effect=TxError(codespace="emissions", code=13, message="unauthorized")
    )
    manager._attempt_submissions = AsyncMock(return_value=None)

    with pytest.raises(TxError, match="unauthorized"):
        await manager.submit_transaction(
            type_url="/emissions.v9.InsertWorkerPayloadRequest",
            msgs=[Mock()],
            fee_tier=FeeTier.STANDARD,
        )

    manager._attempt_submissions.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "simulation_error",
    [
        OutOfGasError("simulation ran out of gas"),
        AccountSequenceMismatchError("sequence mismatch during simulation"),
        InsufficientFeesError("insufficient fees during simulation"),
    ],
)
async def test_submit_transaction_does_not_broadcast_when_simulation_fails_with_known_error(
    simulation_error: Exception,
) -> None:
    manager = _make_manager()
    manager.simulate_transaction = AsyncMock(side_effect=simulation_error)
    manager._attempt_submissions = AsyncMock(return_value=None)

    with pytest.raises(type(simulation_error)):
        await manager.submit_transaction(
            type_url="/emissions.v9.InsertWorkerPayloadRequest",
            msgs=[Mock()],
            fee_tier=FeeTier.STANDARD,
        )

    manager._attempt_submissions.assert_not_called()


@pytest.mark.asyncio
async def test_submit_transaction_falls_back_when_simulation_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _make_manager()
    manager.simulate_transaction = AsyncMock(
        side_effect=GasSimulationUnavailableError("grpc service unavailable")
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


@pytest.mark.asyncio
async def test_submit_transaction_uses_same_account_sequence_for_simulation_and_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        account_seq=12,
    )

    manager.simulate_transaction.assert_awaited_once()
    assert manager.simulate_transaction.await_args.kwargs["account_seq"] == 12
    manager._attempt_submissions.assert_called_once()
    assert manager._attempt_submissions.call_args.kwargs["account_seq"] == 12

    for task in scheduled:
        await task


def test_simulation_unavailable_rpc_errors_are_classified_for_fallback() -> None:
    manager = _make_manager()

    err = manager._exception_from_simulation_error(
        _FakeRpcError(grpc.StatusCode.UNAVAILABLE, "connection unavailable")
    )

    assert isinstance(err, GasSimulationUnavailableError)


@pytest.mark.asyncio
async def test_submit_transaction_keeps_simulated_gas_when_fee_preview_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _make_manager()
    manager.simulate_transaction = AsyncMock(return_value=345678)
    manager._calculate_optimal_fee = AsyncMock(side_effect=RuntimeError("fee preview failed"))
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
