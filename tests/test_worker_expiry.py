"""An expired payload submission (sdk:30 / timeout height) is a terminal,
expected outcome: the worker records the nonce, logs it below ERROR, and never
retries that nonce in a later cycle. A plain, non-expiry TxError must keep its
existing error-level handling. A nonce whose block is not produced yet stays
open and is retried by the next cycle.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from grpclib.const import Status
from grpclib.exceptions import GRPCError

from allora_sdk.rpc_client.tx_manager import TxError, TxExpiredError
from allora_sdk.utils import Context
from allora_sdk.worker.types import WorkerResult
from allora_sdk.worker.worker import AlloraWorker


def _worker(use_case) -> AlloraWorker:
    client = MagicMock()
    client.network = MagicMock(faucet_url=None)
    client.bank = MagicMock()
    client.bank.query = MagicMock()
    client.bank.query.balance = AsyncMock(
        return_value=MagicMock(balance=MagicMock(amount="0"))
    )
    worker = AlloraWorker(
        use_case=use_case,
        client=client,
        address="allo1test",
        topic_id=1,
        polling_interval=999,
    )
    worker._initialized = True
    worker._ctx = Context()
    worker._queue = asyncio.Queue()
    worker._chain_id = "allora-testnet-1"
    return worker


def _use_case_returning(result) -> MagicMock:
    use_case = MagicMock()
    use_case.name.return_value = "inferer"
    use_case.requires_sequential_nonces.return_value = False
    use_case.worker_is_whitelisted = AsyncMock(return_value=True)
    use_case.get_unfulfilled_nonces = AsyncMock(return_value={7})
    use_case.submit = AsyncMock(return_value=result)
    return use_case


def _neutralize(worker: AlloraWorker, monkeypatch) -> None:
    monkeypatch.setattr(worker, "_ensure_initialized", AsyncMock())
    monkeypatch.setattr(worker, "_log_balance", AsyncMock())
    monkeypatch.setattr(worker, "_maybe_faucet_request", AsyncMock())


@pytest.mark.asyncio
async def test_expired_submission_is_recorded_logged_and_not_retried(monkeypatch, caplog) -> None:
    expired = TxExpiredError(
        codespace="sdk",
        code=30,
        message="timeout height 100 already passed at height 101",
    )
    use_case = _use_case_returning(expired)
    worker = _worker(use_case)
    _neutralize(worker, monkeypatch)

    caplog.set_level(logging.DEBUG, logger="allora_sdk")
    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)

    assert use_case.submit.await_count == 1
    assert use_case.submit.await_args.args == (7, None)
    assert 7 in worker.submitted_nonces
    assert "Submission window closed before the payload landed" in caplog.text
    assert "Error submitting for this epoch" not in caplog.text
    assert worker._queue.get_nowait() is expired

    # A later cycle must not re-process the dead nonce.
    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)
    assert use_case.submit.await_count == 1


@pytest.mark.asyncio
async def test_non_expiry_tx_error_keeps_error_level_and_records_nonce(monkeypatch, caplog) -> None:
    error = TxError(codespace="emissions", code=75, message="unfulfilled nonce not found")
    use_case = _use_case_returning(error)
    worker = _worker(use_case)
    _neutralize(worker, monkeypatch)

    caplog.set_level(logging.DEBUG, logger="allora_sdk")
    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)

    assert use_case.submit.await_count == 1
    assert 7 in worker.submitted_nonces
    assert "Error submitting for this epoch" in caplog.text
    assert "Submission window closed before the payload landed" not in caplog.text


@pytest.mark.asyncio
async def test_all_expired_nonces_are_recorded_with_one_submit_each(monkeypatch) -> None:
    expired = TxExpiredError(codespace="sdk", code=30, message="expired")
    use_case = _use_case_returning(expired)
    use_case.get_unfulfilled_nonces = AsyncMock(return_value={7, 8})
    worker = _worker(use_case)
    _neutralize(worker, monkeypatch)

    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)

    assert use_case.submit.await_args_list == [call(7, None), call(8, None)]
    assert 7 in worker.submitted_nonces
    assert 8 in worker.submitted_nonces
    assert worker._queue.qsize() == 2


@pytest.mark.asyncio
async def test_expired_and_landed_nonces_are_both_resolved(monkeypatch) -> None:
    expired = TxExpiredError(codespace="sdk", code=30, message="expired")
    landed = WorkerResult(submission={"y": 1.0}, tx_result=MagicMock(txhash="HASH8"))
    use_case = _use_case_returning(None)
    use_case.get_unfulfilled_nonces = AsyncMock(return_value={7, 8})

    async def submit_by_nonce(nonce, account_seq):
        # Roles RETURN TxErrors (they do not raise), so side effects must too.
        return expired if nonce == 7 else landed

    use_case.submit = AsyncMock(side_effect=submit_by_nonce)
    worker = _worker(use_case)
    _neutralize(worker, monkeypatch)

    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)

    assert 7 in worker.submitted_nonces
    assert 8 in worker.submitted_nonces
    queued = [worker._queue.get_nowait() for _ in range(2)]
    assert queued[0] is expired
    assert queued[1] is landed


@pytest.mark.asyncio
async def test_block_not_ready_error_keeps_nonce_open_and_retries(monkeypatch, caplog) -> None:
    not_ready = GRPCError(
        Status.INVALID_ARGUMENT,
        "requested block height is bigger then the chain length",
        None,
    )
    use_case = _use_case_returning(not_ready)
    worker = _worker(use_case)
    _neutralize(worker, monkeypatch)

    caplog.set_level(logging.DEBUG, logger="allora_sdk")
    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)

    assert use_case.submit.await_count == 1
    assert 7 not in worker.submitted_nonces
    assert "Block for nonce 7 is not produced yet" in caplog.text
    assert "Error submitting for nonce 7" not in caplog.text

    # The nonce stayed open: the next cycle retries it.
    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)
    assert use_case.submit.await_count == 2


@pytest.mark.asyncio
async def test_other_invalid_argument_errors_record_the_nonce(monkeypatch, caplog) -> None:
    other = GRPCError(Status.INVALID_ARGUMENT, "some other invalid argument", None)
    use_case = _use_case_returning(other)
    worker = _worker(use_case)
    _neutralize(worker, monkeypatch)

    caplog.set_level(logging.DEBUG, logger="allora_sdk")
    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)

    assert 7 in worker.submitted_nonces
    assert "Error submitting for nonce 7" in caplog.text
    assert "is not produced yet" not in caplog.text
