"""A gRPC deadline on the tx path is an unknown outcome, not a failure: the
submission loop must re-confirm and retry instead of giving up, and an
abandoned submission must stop rather than broadcast into a dead future."""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, Mock

import pytest

from allora_sdk.rpc_client.tx_manager import BroadcastTimeoutError, TxNotFoundError, TxTimeoutError
from tests.test_tx_manager_fee_granter import _make_manager as _signing_manager, _msg
from tests.test_tx_manager_retry_idempotency import _make_manager, _pending


@pytest.mark.asyncio
async def test_broadcast_deadline_carries_the_hash_of_the_sent_bytes():
    manager = _signing_manager()
    manager.tx_client.broadcast_tx = AsyncMock(side_effect=asyncio.TimeoutError("Deadline exceeded"))

    with pytest.raises(BroadcastTimeoutError) as exc:
        await manager._build_and_broadcast(
            type_url="/cosmos.bank.v1beta1.MsgSend",
            msgs=[_msg()],
            gas_limit=200000,
            fee_multiplier=1.0,
            gas_multiplier=1.0,
            account_seq=7,
        )

    sent = manager.tx_client.broadcast_tx.call_args.args[0].tx_bytes
    assert exc.value.tx_hash == hashlib.sha256(sent).hexdigest().upper()
    assert exc.value.sequence == 7


@pytest.mark.asyncio
async def test_broadcast_deadline_that_landed_is_confirmed_without_rebroadcast():
    manager = _make_manager()
    manager._pre_flight_checks = AsyncMock()
    manager._log_tx_response = Mock()
    manager._raise_for_status = Mock()
    manager._build_and_broadcast = AsyncMock(side_effect=BroadcastTimeoutError("HASH1", 200_000, Mock(), 7))
    landed = Mock()
    landed.tx_response = Mock(code=0, codespace="", txhash="HASH1", raw_log="")
    manager._get_tx = AsyncMock(return_value=landed)

    pending = _pending(manager)
    await manager._attempt_submissions(pending, gas_limit=200_000, account_seq=7)

    assert await pending.wait() is landed.tx_response
    assert manager._build_and_broadcast.await_count == 1


@pytest.mark.asyncio
async def test_broadcast_deadline_not_landed_rebroadcasts_at_the_same_sequence():
    manager = _make_manager()
    manager._pre_flight_checks = AsyncMock()
    manager._log_tx_response = Mock()
    manager._raise_for_status = Mock()
    seqs: list = []

    async def build(type_url, msgs, gas_limit, fee_mult, gas_mult, seq):
        seqs.append(seq)
        if len(seqs) == 1:
            raise BroadcastTimeoutError("HASH1", 200_000, Mock(), 7)
        return ("HASH2", 200_000, Mock(), seq)

    manager._build_and_broadcast = build
    manager._get_tx = AsyncMock(side_effect=TxNotFoundError())
    ok = Mock(tx_response=Mock(code=0, codespace="", txhash="HASH2", raw_log=""))
    manager.wait_for_tx = AsyncMock(return_value=ok)

    pending = _pending(manager)
    await manager._attempt_submissions(pending, gas_limit=200_000, account_seq=7)

    assert await pending.wait() is ok.tx_response
    assert seqs == [7, 7]


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
