"""Reputer nonces are submitted one at a time, oldest first.

The chain stores one loss bundle per (topic, reputer) without a nonce and
closing a nonce consumes and wipes it. When two reputer windows are open at
once, only the oldest may be submitted; a newer one must wait for it to close.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from allora_sdk.utils import Context
from allora_sdk.worker.reputer import Reputer
from allora_sdk.worker.types import AlreadySubmittedError
from allora_sdk.worker.worker import AlloraWorker


def _make_worker(open_nonces, submit_result=None, query_error=None):
    use_case = MagicMock()
    use_case.name.return_value = "reputer"
    use_case.worker_is_whitelisted = AsyncMock(return_value=True)
    use_case.requires_sequential_nonces.return_value = True
    if query_error is not None:
        use_case.get_unfulfilled_nonces = AsyncMock(side_effect=query_error)
    else:
        use_case.get_unfulfilled_nonces = AsyncMock(return_value=set(open_nonces))
    use_case.submit = AsyncMock(return_value=submit_result or Exception("stop after submit"))

    client = MagicMock()
    client.bank.query.balance = AsyncMock(return_value=MagicMock(balance=MagicMock(amount="1000000000")))
    client.auth.query.account_info = AsyncMock(return_value=MagicMock(info=MagicMock(sequence=7)))
    client.network = MagicMock(faucet_url=None)

    worker = AlloraWorker(use_case=use_case, client=client, address="allo1test", topic_id=69, polling_interval=999)
    worker._initialized = True
    worker._chain_id = "allora-testnet-1"
    worker._ctx = Context()
    return worker, use_case


class TestReputerSequentialNonces:
    @pytest.mark.asyncio
    async def test_two_open_windows_submit_only_the_oldest(self):
        worker, use_case = _make_worker(open_nonces={1000, 1100})
        await worker._maybe_submit_impl(worker._ctx)
        assert [c.args[0] for c in use_case.submit.await_args_list] == [1000]

    @pytest.mark.asyncio
    async def test_newer_window_waits_while_submitted_older_nonce_is_open(self):
        worker, use_case = _make_worker(open_nonces={1000, 1100})
        worker.submitted_nonces.add(1000)
        await worker._maybe_submit_impl(worker._ctx)
        use_case.submit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_newer_window_submitted_once_older_nonce_closed(self):
        worker, use_case = _make_worker(open_nonces={1100})
        worker.submitted_nonces.add(1000)
        await worker._maybe_submit_impl(worker._ctx)
        assert [c.args[0] for c in use_case.submit.await_args_list] == [1100]

    @pytest.mark.asyncio
    async def test_window_opened_event_does_not_bypass_open_older_nonce(self):
        worker, use_case = _make_worker(open_nonces={1000, 1100})
        worker.submitted_nonces.add(1000)
        await worker._maybe_submit_impl(worker._ctx, nonce=1100)
        use_case.submit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_window_opened_event_submits_when_it_is_the_oldest(self):
        worker, use_case = _make_worker(open_nonces=set())
        await worker._maybe_submit_impl(worker._ctx, nonce=1100)
        assert [c.args[0] for c in use_case.submit.await_args_list] == [1100]

    @pytest.mark.asyncio
    async def test_unknown_open_nonces_skips_the_cycle(self):
        worker, use_case = _make_worker(open_nonces=set(), query_error=RuntimeError("rpc down"))
        await worker._maybe_submit_impl(worker._ctx, nonce=1100)
        use_case.submit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_given_up_oldest_nonce_blocks_newer_until_it_closes(self):
        # A rejected submission for 1000 marks it as handled; 1100 still has
        # to wait for 1000 to close.
        worker, use_case = _make_worker(
            open_nonces={1000, 1100},
            submit_result=AlreadySubmittedError(codespace="emissions", code=78, tx_hash="", message="once per window"),
        )
        await worker._maybe_submit_impl(worker._ctx)
        assert [c.args[0] for c in use_case.submit.await_args_list] == [1000]
        assert 1000 in worker.submitted_nonces
        use_case.submit.reset_mock()
        await worker._maybe_submit_impl(worker._ctx)
        use_case.submit.assert_not_awaited()

    def test_reputer_declares_sequential_nonces(self):
        reputer = Reputer(wallet=MagicMock(), client=MagicMock(), topic_id=69, reputer_fn=lambda ctx, v: 0.0)
        assert reputer.requires_sequential_nonces() is True
