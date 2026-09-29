"""A gRPC deadline on the tx path is an unknown outcome, not a failure: the
submission loop must re-confirm and retry instead of giving up, and an
abandoned submission must stop rather than broadcast into a dead future."""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, Mock

import pytest

from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.keypairs import PrivateKey

from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.protos.cosmos.bank.v1beta1 import MsgSend
from allora_sdk.rpc_client.tx_manager import (
    FeeTier,
    PendingTx,
    TxManager,
    TxNotFoundError,
    TxTimeoutError,
)


def _make_manager(wallet=None) -> TxManager:
    if wallet is None:
        wallet = Mock()
        wallet.address.return_value = "allo1sender"
    config = AlloraNetworkConfig.testnet()
    config.use_dynamic_gas_price = False
    config.congestion_aware_fees = False
    auth_client = Mock()
    auth_client.account_info = AsyncMock(return_value=Mock(info=Mock(sequence=0, account_number=1)))
    return TxManager(
        wallet=wallet,
        tx_client=Mock(),
        auth_client=auth_client,
        bank_client=Mock(),
        feemarket_client=Mock(),
        config=config,
    )


def _pending(manager: TxManager) -> PendingTx:
    return PendingTx(
        manager,
        parent_tx_id=1,
        type_url="/emissions.v10.InsertWorkerPayloadRequest",
        msgs=[Mock()],
        fee_tier=FeeTier.STANDARD,
        max_retries=2,
        timeout=None,
    )


def _msgs():
    return [MsgSend(from_address="allo1sender", to_address="allo1receiver")]


def _signing_manager() -> TxManager:
    manager = _make_manager(wallet=LocalWallet(PrivateKey(), prefix="allo"))
    manager._pre_flight_checks = AsyncMock()
    manager._log_tx_response = Mock()
    manager._raise_for_status = Mock()
    return manager


def _signing_pending(manager: TxManager) -> PendingTx:
    pending = _pending(manager)
    pending.type_url = "/cosmos.bank.v1beta1.MsgSend"
    pending.msgs = _msgs()
    return pending


def _accepted():
    return Mock(tx_response=Mock(code=0, codespace="", txhash="HASH2", raw_log=""))


@pytest.mark.asyncio
async def test_broadcast_deadline_returns_the_hash_of_the_sent_bytes():
    manager = _make_manager(wallet=LocalWallet(PrivateKey(), prefix="allo"))
    manager.tx_client.broadcast_tx = AsyncMock(side_effect=asyncio.TimeoutError("Deadline exceeded"))

    tx_hash, _, _, seq = await manager._build_and_broadcast(
        type_url="/cosmos.bank.v1beta1.MsgSend",
        msgs=_msgs(),
        gas_limit=200000,
        fee_multiplier=1.0,
        gas_multiplier=1.0,
        account_seq=7,
    )

    sent = manager.tx_client.broadcast_tx.call_args.args[0].tx_bytes
    assert tx_hash == hashlib.sha256(sent).hexdigest().upper()
    assert seq == 7


@pytest.mark.asyncio
async def test_broadcast_deadline_that_landed_is_confirmed_without_rebroadcast():
    manager = _signing_manager()
    manager.tx_client.broadcast_tx = AsyncMock(side_effect=asyncio.TimeoutError("Deadline exceeded"))
    landed = Mock(tx_response=Mock(code=0, codespace="", raw_log=""))
    manager.tx_client.get_tx = AsyncMock(return_value=landed)

    pending = _signing_pending(manager)
    await manager._attempt_submissions(pending, gas_limit=200_000, account_seq=7)

    assert await pending.wait() is landed.tx_response
    assert manager.tx_client.broadcast_tx.await_count == 1
    sent = manager.tx_client.broadcast_tx.call_args.args[0].tx_bytes
    assert manager.tx_client.get_tx.call_args.args[0].hash == hashlib.sha256(sent).hexdigest().upper()


@pytest.mark.asyncio
async def test_broadcast_deadline_not_landed_rebroadcasts_at_the_same_sequence():
    manager = _signing_manager()
    manager.tx_client.broadcast_tx = AsyncMock(side_effect=[asyncio.TimeoutError("Deadline exceeded"), _accepted()])
    ok = _accepted()
    manager.wait_for_tx = AsyncMock(side_effect=[TxTimeoutError(), ok])
    manager._get_tx = AsyncMock(side_effect=TxNotFoundError())
    seqs: list = []
    build = manager._build_and_broadcast

    async def spy(*args):
        seqs.append(args[-1])
        return await build(*args)

    manager._build_and_broadcast = spy

    pending = _signing_pending(manager)
    await manager._attempt_submissions(pending, gas_limit=200_000, account_seq=7)

    assert await pending.wait() is ok.tx_response
    assert seqs == [7, 7]


@pytest.mark.asyncio
async def test_pre_broadcast_query_deadline_is_retried():
    manager = _signing_manager()
    info = Mock(info=Mock(sequence=7, account_number=1))
    manager.auth_client.account_info = AsyncMock(side_effect=[asyncio.TimeoutError("Deadline exceeded"), info])
    manager.tx_client.broadcast_tx = AsyncMock(return_value=_accepted())
    ok = _accepted()
    manager.wait_for_tx = AsyncMock(return_value=ok)

    pending = _signing_pending(manager)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    assert await pending.wait() is ok.tx_response
    assert manager.tx_client.broadcast_tx.await_count == 1


@pytest.mark.asyncio
async def test_persistent_pre_broadcast_deadline_ends_in_tx_timeout():
    manager = _signing_manager()
    manager.auth_client.account_info = AsyncMock(side_effect=asyncio.TimeoutError("Deadline exceeded"))
    manager.tx_client.broadcast_tx = AsyncMock()

    pending = _signing_pending(manager)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    with pytest.raises(TxTimeoutError):
        await pending.wait()
    assert manager.tx_client.broadcast_tx.await_count == 0


@pytest.mark.asyncio
async def test_slow_get_tx_poll_keeps_waiting():
    manager = _make_manager()
    found = Mock(tx_response=Mock())
    manager.tx_client.get_tx = AsyncMock(side_effect=[asyncio.TimeoutError("Deadline exceeded"), found])

    assert await manager.wait_for_tx("HASH1", timeout=5, poll_period=0.01) is found


@pytest.mark.asyncio
async def test_slow_get_tx_polls_still_end_in_tx_timeout():
    manager = _make_manager()
    manager.tx_client.get_tx = AsyncMock(side_effect=asyncio.TimeoutError("Deadline exceeded"))

    with pytest.raises(TxTimeoutError):
        await manager.wait_for_tx("HASH1", timeout=0.05, poll_period=0.01)


@pytest.mark.asyncio
async def test_slow_get_tx_poll_is_bounded_by_the_wait_budget():
    manager = _make_manager()

    async def hang(_):
        await asyncio.sleep(10)

    manager.tx_client.get_tx = hang

    with pytest.raises(TxTimeoutError):
        await asyncio.wait_for(manager.wait_for_tx("HASH1", timeout=0.1, poll_period=0.01), timeout=2)


@pytest.mark.asyncio
async def test_cancelled_waiter_stops_the_submission_task():
    manager = _make_manager()
    manager._pre_flight_checks = AsyncMock()
    release = asyncio.Event()

    async def slow_build(*_):
        await release.wait()
        return ("HASH1", 200_000, Mock(), 7)

    manager._build_and_broadcast = slow_build
    pending = _pending(manager)
    pending._task = asyncio.create_task(manager._attempt_submissions(pending, gas_limit=200_000, account_seq=7))
    await asyncio.sleep(0)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(pending.wait(), timeout=0.05)
    release.set()
    await asyncio.gather(pending._task, return_exceptions=True)

    assert pending._task.cancelled()


@pytest.mark.asyncio
async def test_late_outcome_after_abandon_does_not_raise():
    manager = _make_manager()
    pending = _pending(manager)
    pending._final_future.cancel()

    pending._set_result(Mock())
    pending._set_exception(RuntimeError("late"))


@pytest.mark.asyncio
async def test_broadcast_deadline_confirms_inclusion_before_rebroadcasting():
    """A timed-out broadcast must be confirmed by a wait_for_tx inclusion poll
    before any same-sequence re-broadcast is attempted."""
    manager = _signing_manager()
    events: list[str] = []

    async def broadcast(_req):
        events.append("broadcast")
        if events.count("broadcast") == 1:
            raise asyncio.TimeoutError("Deadline exceeded")
        return _accepted()

    manager.tx_client.broadcast_tx = broadcast

    async def wait_for_tx(_hash, **_kwargs):
        events.append("wait_for_tx")
        if events.count("wait_for_tx") == 1:
            raise TxTimeoutError()
        return _accepted()

    manager.wait_for_tx = wait_for_tx
    manager._get_tx = AsyncMock(side_effect=TxNotFoundError())

    pending = _signing_pending(manager)
    await manager._attempt_submissions(pending, gas_limit=200_000, account_seq=7)

    assert await pending.wait() is not None
    assert events == ["broadcast", "wait_for_tx", "broadcast", "wait_for_tx"]
