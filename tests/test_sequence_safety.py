"""Regression tests for PR37 sequence-safety changes.

- Worker: _maybe_submit is serialized by lock (polling + websocket cannot race).
- Reputer: stake top-up runs after payload submission (sequence-critical path).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from allora_sdk.rpc_client.tx_manager import AccountSequenceMismatchError
from allora_sdk.utils import Context
from allora_sdk.worker.reputer import Reputer
from allora_sdk.worker.worker import AlloraWorker


# ---------------------------------------------------------------------------
# Worker: concurrent trigger serialization
# ---------------------------------------------------------------------------


class TestWorkerSubmitLock:
    """Verify _maybe_submit is serialized; concurrent polling and websocket triggers cannot race."""

    @pytest.mark.asyncio
    async def test_maybe_submit_serialized_no_concurrent_impl(self):
        """Concurrent _maybe_submit calls run one at a time; never overlap in _maybe_submit_impl."""
        enter_event = asyncio.Event()
        exit_event = asyncio.Event()
        concurrent_count = 0
        max_concurrent = 0

        async def blocking_impl(ctx, nonce=None):
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            enter_event.set()
            await exit_event.wait()
            concurrent_count -= 1

        mock_use_case = MagicMock()
        mock_use_case.name.return_value = "inferer"
        mock_use_case.worker_is_whitelisted = AsyncMock(return_value=True)
        mock_use_case.get_unfulfilled_nonces = AsyncMock(return_value=set())
        mock_use_case.submit = AsyncMock()

        mock_client = MagicMock()
        mock_client.raise_for_chain_id_mismatch = AsyncMock(return_value="allora-testnet-1")
        mock_client.emissions = MagicMock()
        mock_client.emissions.query = MagicMock()
        mock_client.emissions.query.get_topic = AsyncMock()
        mock_client.bank = MagicMock()
        mock_client.bank.query = MagicMock()
        mock_client.bank.query.balance = AsyncMock(return_value=MagicMock(balance=MagicMock(amount="1000000000")))
        mock_client.auth = MagicMock()
        mock_client.auth.query = MagicMock()
        mock_client.auth.query.account_info = AsyncMock(
            return_value=MagicMock(info=MagicMock(sequence=0))
        )
        mock_client.network = MagicMock(faucet_url=None)
        mock_client.events = MagicMock()
        mock_client.events.subscribe_new_block_events_typed = AsyncMock(return_value="sub-id")
        mock_client.events.unsubscribe = AsyncMock()

        topic_resp = MagicMock()
        topic_resp.topic = MagicMock(metadata="test")
        mock_client.emissions.query.get_topic.return_value = topic_resp

        worker = AlloraWorker(
            use_case=mock_use_case,
            client=mock_client,
            address="allo1test",
            topic_id=69,
            polling_interval=999,
        )
        worker._initialized = True
        worker._chain_id = "allora-testnet-1"

        with patch(
            "allora_sdk.worker.worker.AlloraWorker._maybe_submit_impl",
            side_effect=blocking_impl,
        ):
            ctx = Context()

            task1 = asyncio.create_task(worker._maybe_submit(ctx))
            await enter_event.wait()
            exit_event.clear()

            task2 = asyncio.create_task(worker._maybe_submit(ctx))
            await asyncio.sleep(0.05)

            assert max_concurrent == 1
            assert concurrent_count == 1

            exit_event.set()
            await asyncio.gather(task1, task2)

        assert max_concurrent == 1

    @pytest.mark.asyncio
    async def test_maybe_submit_uses_base_sequence_with_increment(self):
        """Nonce submissions should use base sequence + index in one cycle."""
        mock_use_case = MagicMock()
        mock_use_case.name.return_value = "inferer"
        mock_use_case.worker_is_whitelisted = AsyncMock(return_value=True)
        mock_use_case.get_unfulfilled_nonces = AsyncMock(return_value={101, 202})
        mock_use_case.submit = AsyncMock(return_value=Exception("simulated submit failure"))

        mock_client = MagicMock()
        mock_client.raise_for_chain_id_mismatch = AsyncMock(return_value="allora-testnet-1")
        mock_client.emissions = MagicMock()
        mock_client.emissions.query = MagicMock()
        mock_client.emissions.query.get_topic = AsyncMock(
            return_value=MagicMock(topic=MagicMock(metadata="test"))
        )
        mock_client.bank = MagicMock()
        mock_client.bank.query = MagicMock()
        mock_client.bank.query.balance = AsyncMock(return_value=MagicMock(balance=MagicMock(amount="1000000000")))
        mock_client.auth = MagicMock()
        mock_client.auth.query = MagicMock()
        mock_client.auth.query.account_info = AsyncMock(return_value=MagicMock(info=MagicMock(sequence=10)))
        mock_client.network = MagicMock(faucet_url=None)
        mock_client.events = MagicMock()
        mock_client.events.subscribe_new_block_events_typed = AsyncMock(return_value="sub-id")
        mock_client.events.unsubscribe = AsyncMock()

        worker = AlloraWorker(
            use_case=mock_use_case,
            client=mock_client,
            address="allo1test",
            topic_id=69,
            polling_interval=999,
        )
        worker._initialized = True
        worker._chain_id = "allora-testnet-1"
        worker._ctx = Context()
        worker._queue = asyncio.Queue()

        await worker._maybe_submit_impl(worker._ctx)

        mock_client.auth.query.account_info.assert_awaited_once()
        assert mock_use_case.submit.await_args_list == [call(101, 10), call(202, 11)]

    @pytest.mark.asyncio
    async def test_maybe_submit_stops_after_sequence_mismatch(self):
        """Stop nonce loop after sequence mismatch to avoid stale sequence submissions."""
        mock_use_case = MagicMock()
        mock_use_case.name.return_value = "inferer"
        mock_use_case.worker_is_whitelisted = AsyncMock(return_value=True)
        mock_use_case.get_unfulfilled_nonces = AsyncMock(return_value={101, 202, 303})
        mock_use_case.submit = AsyncMock(
            side_effect=[
                AccountSequenceMismatchError("account sequence mismatch"),
                Exception("should not submit second nonce"),
            ]
        )

        mock_client = MagicMock()
        mock_client.raise_for_chain_id_mismatch = AsyncMock(return_value="allora-testnet-1")
        mock_client.emissions = MagicMock()
        mock_client.emissions.query = MagicMock()
        mock_client.emissions.query.get_topic = AsyncMock(
            return_value=MagicMock(topic=MagicMock(metadata="test"))
        )
        mock_client.bank = MagicMock()
        mock_client.bank.query = MagicMock()
        mock_client.bank.query.balance = AsyncMock(
            return_value=MagicMock(balance=MagicMock(amount="1000000000"))
        )
        mock_client.auth = MagicMock()
        mock_client.auth.query = MagicMock()
        mock_client.auth.query.account_info = AsyncMock(
            return_value=MagicMock(info=MagicMock(sequence=10))
        )
        mock_client.network = MagicMock(faucet_url=None)
        mock_client.events = MagicMock()
        mock_client.events.subscribe_new_block_events_typed = AsyncMock(return_value="sub-id")
        mock_client.events.unsubscribe = AsyncMock()

        worker = AlloraWorker(
            use_case=mock_use_case,
            client=mock_client,
            address="allo1test",
            topic_id=69,
            polling_interval=999,
        )
        worker._initialized = True
        worker._chain_id = "allora-testnet-1"
        worker._ctx = Context()
        worker._queue = asyncio.Queue()
        reset_sequence = AsyncMock()
        worker._account_sequence_reset = reset_sequence

        await worker._maybe_submit_impl(worker._ctx)

        assert mock_use_case.submit.await_count == 1
        reset_sequence.assert_awaited_once_with("allo1test")


# ---------------------------------------------------------------------------
# Reputer: sequence-safe path (payload before stake)
# ---------------------------------------------------------------------------


class TestReputerSequenceSafePath:
    """Verify stake top-up runs after payload submission, not before."""

    @pytest.mark.asyncio
    async def test_stake_after_payload_not_before(self):
        """_maybe_stake is called only after successful insert_reputer_payload."""
        call_order = []

        async def mock_insert(*args, **kwargs):
            call_order.append("insert_reputer_payload")
            pending = MagicMock()
            tx_resp = MagicMock()
            tx_resp.code = 0
            tx_resp.txhash = "abc123"
            pending.wait = AsyncMock(return_value=tx_resp)
            return pending

        async def mock_maybe_stake(self):
            call_order.append("_maybe_stake")

        mock_wallet = MagicMock()
        mock_wallet.address.return_value = MagicMock(__str__=lambda _: "allo1reputer")

        mock_client = MagicMock()
        mock_client.emissions = MagicMock()
        mock_client.emissions.tx = MagicMock()
        mock_client.emissions.tx.insert_reputer_payload = AsyncMock(side_effect=mock_insert)
        mock_client.emissions.query = MagicMock()
        mock_client.emissions.query.get_network_inferences_at_block = AsyncMock(
            return_value=MagicMock(
                network_inferences=MagicMock(
                    combined_value="1.0",
                    naive_value="1.0",
                    inferer_values=[],
                    forecaster_values=[],
                    one_out_inferer_values=[],
                    one_out_forecaster_values=[],
                    one_in_forecaster_values=[],
                    one_out_inferer_forecaster_values=[],
                )
            )
        )

        reputer = Reputer(
            wallet=mock_wallet,
            client=mock_client,
            topic_id=69,
            ground_truth_fn=lambda n: 1.0,
            min_stake_uallo=100,
        )
        reputer.loss_fn = lambda g, p: 0.0

        with patch.object(Reputer, "_maybe_stake", mock_maybe_stake):
            result = await reputer.submit(nonce=100, account_seq=0)

        assert call_order == ["insert_reputer_payload", "_maybe_stake"]
        assert result is not None
        assert hasattr(result, "submission")
        assert hasattr(result, "tx_result")

    @pytest.mark.asyncio
    async def test_stake_not_called_on_payload_failure(self):
        """_maybe_stake is not called when payload submission fails."""
        call_order = []

        async def mock_insert_fail(*args, **kwargs):
            call_order.append("insert_reputer_payload")
            from allora_sdk.rpc_client.tx_manager import TxError

            raise TxError(code=5, message="simulated fail", tx_hash="", codespace="")

        async def mock_maybe_stake(self):
            call_order.append("_maybe_stake")

        mock_wallet = MagicMock()
        mock_wallet.address.return_value = MagicMock(__str__=lambda _: "allo1reputer")

        mock_client = MagicMock()
        mock_client.emissions = MagicMock()
        mock_client.emissions.tx = MagicMock()
        mock_client.emissions.tx.insert_reputer_payload = AsyncMock(side_effect=mock_insert_fail)
        mock_client.emissions.query = MagicMock()
        mock_client.emissions.query.get_network_inferences_at_block = AsyncMock(
            return_value=MagicMock(
                network_inferences=MagicMock(
                    combined_value="1.0",
                    naive_value="1.0",
                    inferer_values=[],
                    forecaster_values=[],
                    one_out_inferer_values=[],
                    one_out_forecaster_values=[],
                    one_in_forecaster_values=[],
                    one_out_inferer_forecaster_values=[],
                )
            )
        )

        reputer = Reputer(
            wallet=mock_wallet,
            client=mock_client,
            topic_id=69,
            ground_truth_fn=lambda n: 1.0,
            min_stake_uallo=100,
        )
        reputer.loss_fn = lambda g, p: 0.0

        with patch.object(Reputer, "_maybe_stake", mock_maybe_stake):
            result = await reputer.submit(nonce=100, account_seq=0)

        assert call_order == ["insert_reputer_payload"]
        assert result is not None
        from allora_sdk.rpc_client.tx_manager import TxError

        assert isinstance(result, TxError)

    @pytest.mark.asyncio
    async def test_staking_txerror_does_not_map_to_already_submitted(self):
        """Staking failures should not be classified by payload AlreadySubmitted logic."""
        from allora_sdk.rpc_client.tx_manager import TxError

        async def mock_insert(*args, **kwargs):
            pending = MagicMock()
            tx_resp = MagicMock()
            tx_resp.code = 0
            tx_resp.txhash = "abc123"
            pending.wait = AsyncMock(return_value=tx_resp)
            return pending

        async def mock_maybe_stake_raise(self):
            raise TxError(
                code=68,
                message="staking account sequence mismatch",
                tx_hash="stake123",
                codespace="sdk",
            )

        mock_wallet = MagicMock()
        mock_wallet.address.return_value = MagicMock(__str__=lambda _: "allo1reputer")

        mock_client = MagicMock()
        mock_client.emissions = MagicMock()
        mock_client.emissions.tx = MagicMock()
        mock_client.emissions.tx.insert_reputer_payload = AsyncMock(side_effect=mock_insert)
        mock_client.emissions.query = MagicMock()
        mock_client.emissions.query.get_network_inferences_at_block = AsyncMock(
            return_value=MagicMock(
                network_inferences=MagicMock(
                    combined_value="1.0",
                    naive_value="1.0",
                    inferer_values=[],
                    forecaster_values=[],
                    one_out_inferer_values=[],
                    one_out_forecaster_values=[],
                    one_in_forecaster_values=[],
                    one_out_inferer_forecaster_values=[],
                )
            )
        )

        reputer = Reputer(
            wallet=mock_wallet,
            client=mock_client,
            topic_id=69,
            ground_truth_fn=lambda n: 1.0,
            min_stake_uallo=100,
        )
        reputer.loss_fn = lambda g, p: 0.0

        with patch.object(Reputer, "_maybe_stake", mock_maybe_stake_raise):
            result = await reputer.submit(nonce=100, account_seq=0)

        assert result is not None
        assert hasattr(result, "submission")
        assert hasattr(result, "tx_result")
