"""Tests for chain v0.18 adaptation: permission, nonce, topic fields, guards."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from grpclib.const import Status
from grpclib.exceptions import GRPCError

from allora_sdk.rpc_client.compat import (
    can_submit_forecast,
    can_submit_inference,
    epoch_nonce,
    payload_permission,
    reset_permission_query_fallback,
)
from allora_sdk.rpc_client.client_emissions import EmissionsTxs
from allora_sdk.rpc_client.tx_manager import (
    FeeTier,
    OutOfGasError,
    TxError,
    TxManager,
)
from allora_sdk.rpc_client.config import AlloraNetworkConfig


@pytest.fixture(autouse=True)
def _reset_permission_fallback():
    reset_permission_query_fallback()
    yield
    reset_permission_query_fallback()


# ---------------------------------------------------------------------------
# epoch_nonce
# ---------------------------------------------------------------------------


def test_epoch_nonce_prefers_nonzero_nonce():
    event = SimpleNamespace(nonce=42, nonce_block_height=7)
    assert epoch_nonce(event, "nonce_block_height") == 42


def test_epoch_nonce_falls_back_when_nonce_zero():
    event = SimpleNamespace(nonce=0, nonce_block_height=99)
    assert epoch_nonce(event, "nonce_block_height") == 99


def test_epoch_nonce_falls_back_when_nonce_absent():
    event = SimpleNamespace(nonce_block_height=55)
    assert epoch_nonce(event, "nonce_block_height") == 55


def test_epoch_nonce_rewards_legacy_block_height():
    event = SimpleNamespace(nonce=0, block_height=1000, block_height_tx=1005)
    assert epoch_nonce(event, "block_height") == 1000


def test_epoch_nonce_returns_zero_when_nothing_usable():
    event = SimpleNamespace(nonce=0)
    assert epoch_nonce(event, "nonce_block_height") == 0


# ---------------------------------------------------------------------------
# permission queries + v0.17 fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_can_submit_inference_uses_new_query():
    query = Mock()
    query.can_submit_inference_payload = AsyncMock(
        return_value=SimpleNamespace(can_submit_inference_payload=True)
    )
    assert await can_submit_inference(query, "allo1a", 1) is True
    query.can_submit_inference_payload.assert_awaited_once()
    query.can_submit_worker_payload = AsyncMock()
    query.can_submit_worker_payload.assert_not_called()


@pytest.mark.asyncio
async def test_can_submit_forecast_uses_new_query():
    query = Mock()
    query.can_submit_forecaster_payload = AsyncMock(
        return_value=SimpleNamespace(can_submit_forecaster_payload=False)
    )
    assert await can_submit_forecast(query, "allo1a", 1) is False


@pytest.mark.asyncio
async def test_permission_falls_back_on_grpc_unimplemented():
    query = Mock()
    query.can_submit_forecaster_payload = AsyncMock(
        side_effect=GRPCError(Status.UNIMPLEMENTED, "unknown method", None)
    )
    query.can_submit_inference_payload = AsyncMock(
        side_effect=GRPCError(Status.UNIMPLEMENTED, "unknown method", None)
    )
    query.can_submit_worker_payload = AsyncMock(
        return_value=SimpleNamespace(can_submit_worker_payload=True)
    )
    assert await can_submit_forecast(query, "allo1a", 1) is True
    query.can_submit_worker_payload.assert_awaited_once()


@pytest.mark.asyncio
async def test_permission_falls_back_on_http_404():
    err = Exception("not found")
    err.response = SimpleNamespace(status_code=404)  # type: ignore[attr-defined]
    query = Mock()
    query.can_submit_inference_payload = AsyncMock(side_effect=err)
    query.can_submit_worker_payload = AsyncMock(
        return_value=SimpleNamespace(can_submit_worker_payload=False)
    )
    assert await can_submit_inference(query, "allo1a", 1) is False


@pytest.mark.asyncio
async def test_permission_remembers_legacy_after_unimplemented():
    query = Mock()
    query.can_submit_forecaster_payload = AsyncMock(
        side_effect=GRPCError(Status.UNIMPLEMENTED, "unknown method", None)
    )
    query.can_submit_inference_payload = AsyncMock(
        side_effect=GRPCError(Status.UNIMPLEMENTED, "unknown method", None)
    )
    query.can_submit_worker_payload = AsyncMock(
        return_value=SimpleNamespace(can_submit_worker_payload=True)
    )
    assert await can_submit_forecast(query, "allo1a", 1) is True
    # Second call must not probe the missing method again.
    query.can_submit_forecaster_payload.reset_mock()
    query.can_submit_inference_payload.reset_mock()
    assert await can_submit_forecast(query, "allo1a", 1) is True
    query.can_submit_forecaster_payload.assert_not_called()
    query.can_submit_inference_payload.assert_not_called()
    assert query.can_submit_worker_payload.await_count == 2


@pytest.mark.asyncio
async def test_forecast_does_not_use_worker_query_when_split_rpc_exists():
    unimplemented = GRPCError(Status.UNIMPLEMENTED, "unknown method", None)
    query = Mock()
    query.can_submit_forecaster_payload = AsyncMock(side_effect=unimplemented)
    query.can_submit_inference_payload = AsyncMock(
        return_value=SimpleNamespace(can_submit_inference_payload=True)
    )
    query.can_submit_worker_payload = AsyncMock()
    with pytest.raises(GRPCError):
        await can_submit_forecast(query, "allo1a", 1)
    query.can_submit_worker_payload.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_latch_is_per_query_client():
    unimplemented = GRPCError(Status.UNIMPLEMENTED, "unknown method", None)
    query_v017 = Mock()
    query_v017.can_submit_forecaster_payload = AsyncMock(side_effect=unimplemented)
    query_v017.can_submit_inference_payload = AsyncMock(side_effect=unimplemented)
    query_v017.can_submit_worker_payload = AsyncMock(
        return_value=SimpleNamespace(can_submit_worker_payload=True)
    )

    query_v018 = Mock()
    query_v018.can_submit_forecaster_payload = AsyncMock(
        return_value=SimpleNamespace(can_submit_forecaster_payload=False)
    )
    query_v018.can_submit_worker_payload = AsyncMock()

    assert await can_submit_forecast(query_v017, "allo1a", 1) is True
    assert await can_submit_forecast(query_v018, "allo1a", 1) is False
    query_v018.can_submit_forecaster_payload.assert_awaited_once()
    query_v018.can_submit_worker_payload.assert_not_called()


@pytest.mark.asyncio
async def test_permission_timeout_propagates():
    query = Mock()
    query.can_submit_inference_payload = AsyncMock(side_effect=TimeoutError("deadline"))
    with pytest.raises(TimeoutError):
        await can_submit_inference(query, "allo1a", 1)


@pytest.mark.asyncio
async def test_payload_permission_requires_both_parts():
    query = Mock()
    query.can_submit_inference_payload = AsyncMock(
        return_value=SimpleNamespace(can_submit_inference_payload=True)
    )
    query.can_submit_forecaster_payload = AsyncMock(
        return_value=SimpleNamespace(can_submit_forecaster_payload=False)
    )
    assert (
        await payload_permission(
            query, "allo1a", 1, has_inference=True, has_forecast=True
        )
        is False
    )


@pytest.mark.asyncio
async def test_payload_permission_empty_is_false():
    query = Mock()
    assert (
        await payload_permission(
            query, "allo1a", 1, has_inference=False, has_forecast=False
        )
        is False
    )


# ---------------------------------------------------------------------------
# EmissionsTxs local guards
# ---------------------------------------------------------------------------


def _make_txs(*, query: Mock | None = None) -> EmissionsTxs:
    wallet = Mock()
    wallet.address.return_value = "allo1sender"
    pubkey = Mock()
    pubkey.public_key_hex = "aa" * 33
    wallet.public_key.return_value = pubkey
    signer = Mock()
    signer.sign_digest.return_value = b"\x00" * 64
    wallet.signer.return_value = signer

    manager = Mock(spec=TxManager)
    manager.wallet = wallet
    manager.submit_transaction = AsyncMock(return_value=Mock())

    return EmissionsTxs(txs=manager, query=query)


@pytest.mark.asyncio
async def test_insert_worker_payload_rejects_empty():
    txs = _make_txs()
    with pytest.raises(ValueError, match="At least one"):
        await txs.insert_worker_payload(
            topic_id=1,
            inference_value=[],
            nonce=10,
            forecast_elements=[],
        )
    txs._txs.submit_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_insert_worker_payload_rejects_zero_nonce():
    txs = _make_txs()
    with pytest.raises(ValueError, match="greater than 0"):
        await txs.insert_worker_payload(
            topic_id=1,
            inference_value=None,
            nonce=0,
            forecast_elements=[{"inferer": "allo1a", "value": "1.0"}],
        )
    txs._txs.submit_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_insert_worker_payload_rejects_duplicate_inferer():
    txs = _make_txs()
    with pytest.raises(ValueError, match="duplicate inferer"):
        await txs.insert_worker_payload(
            topic_id=1,
            inference_value=None,
            nonce=10,
            forecast_elements=[
                {"inferer": "allo1a", "value": "1.0"},
                {"inferer": "allo1a", "value": "2.0"},
            ],
        )
    txs._txs.submit_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_insert_worker_payload_rejects_denied_permission():
    query = Mock()
    query.can_submit_forecaster_payload = AsyncMock(
        return_value=SimpleNamespace(can_submit_forecaster_payload=False)
    )
    txs = _make_txs(query=query)
    with pytest.raises(ValueError, match="Not permitted"):
        await txs.insert_worker_payload(
            topic_id=1,
            inference_value=None,
            nonce=10,
            forecast_elements=[{"inferer": "allo1a", "value": "1.0"}],
        )
    txs._txs.submit_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_create_topic_requires_cap_keyword():
    txs = _make_txs()
    with pytest.raises(TypeError):
        await txs.create_topic(  # type: ignore[call-arg]
            metadata="{}",
            loss_method="mse",
            epoch_length=100,
            ground_truth_lag=10,
            worker_submission_window=20,
        )


@pytest.mark.asyncio
async def test_create_topic_sets_new_fields_and_checks_permission():
    query = Mock()
    query.can_create_topic = AsyncMock(
        return_value=SimpleNamespace(can_create_topic=True)
    )
    query.get_params = AsyncMock(
        return_value=SimpleNamespace(
            params=SimpleNamespace(
                min_top_inferers_to_reward=5,
                max_top_inferers_to_reward=32,
            )
        )
    )
    txs = _make_txs(query=query)

    await txs.create_topic(
        metadata="{}",
        loss_method="mse",
        epoch_length=100,
        ground_truth_lag=10,
        worker_submission_window=20,
        max_top_inferers_to_reward=8,
        enable_forecaster_whitelist=True,
    )

    txs._txs.submit_transaction.assert_awaited_once()
    call = txs._txs.submit_transaction.await_args
    msgs = call.kwargs.get("msgs") or call.args[1]
    assert msgs[0].max_top_inferers_to_reward == 8
    assert msgs[0].enable_forecaster_whitelist is True


@pytest.mark.asyncio
async def test_create_topic_rejects_out_of_range_cap_when_floor_positive():
    query = Mock()
    query.can_create_topic = AsyncMock(
        return_value=SimpleNamespace(can_create_topic=True)
    )
    query.get_params = AsyncMock(
        return_value=SimpleNamespace(
            params=SimpleNamespace(
                min_top_inferers_to_reward=5,
                max_top_inferers_to_reward=32,
            )
        )
    )
    txs = _make_txs(query=query)
    with pytest.raises(ValueError, match="outside the live range"):
        await txs.create_topic(
            metadata="{}",
            loss_method="mse",
            epoch_length=100,
            ground_truth_lag=10,
            worker_submission_window=20,
            max_top_inferers_to_reward=0,
        )
    txs._txs.submit_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_create_topic_rejects_cap_above_ceiling_when_floor_zero():
    """Zero floor still enforces the ceiling (chain validateMaxTopInferersToReward)."""
    query = Mock()
    query.can_create_topic = AsyncMock(
        return_value=SimpleNamespace(can_create_topic=True)
    )
    query.get_params = AsyncMock(
        return_value=SimpleNamespace(
            params=SimpleNamespace(
                min_top_inferers_to_reward=0,
                max_top_inferers_to_reward=32,
            )
        )
    )
    txs = _make_txs(query=query)
    with pytest.raises(ValueError, match="outside the live range"):
        await txs.create_topic(
            metadata="{}",
            loss_method="mse",
            epoch_length=100,
            ground_truth_lag=10,
            worker_submission_window=20,
            max_top_inferers_to_reward=33,
        )
    txs._txs.submit_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_create_topic_rejects_when_cannot_create():
    query = Mock()
    query.can_create_topic = AsyncMock(
        return_value=SimpleNamespace(can_create_topic=False)
    )
    txs = _make_txs(query=query)
    with pytest.raises(ValueError, match="not permitted to create"):
        await txs.create_topic(
            metadata="{}",
            loss_method="mse",
            epoch_length=100,
            ground_truth_lag=10,
            worker_submission_window=20,
            max_top_inferers_to_reward=8,
        )
    txs._txs.submit_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_update_topic_sends_full_mutable_set_and_cap():
    query = Mock()
    topic = SimpleNamespace(
        creator="allo1sender",
        metadata="old",
        loss_method="mse",
        alpha_regret="0.1",
        merit_sortition_alpha="0.1",
        p_norm="3.0",
        c_norm="0.75",
        max_labels_per_submission=1,
        label_whitelist=["y"],
        label_default_value="0",
        max_top_inferers_to_reward=10,
    )
    query.get_topic = AsyncMock(return_value=SimpleNamespace(topic=topic))
    query.get_params = AsyncMock(
        return_value=SimpleNamespace(
            params=SimpleNamespace(
                min_top_inferers_to_reward=5,
                max_top_inferers_to_reward=32,
            )
        )
    )
    txs = _make_txs(query=query)

    await txs.update_topic(topic_id=3, max_top_inferers_to_reward=12)

    msgs = txs._txs.submit_transaction.await_args.kwargs["msgs"]
    msg = msgs[0]
    assert msg.max_top_inferers_to_reward == 12
    assert msg.metadata == "old"
    assert msg.label_whitelist == ["y"]
    assert msg.loss_method == "mse"


@pytest.mark.asyncio
async def test_update_topic_allows_copied_zero_cap_when_floor_zero():
    query = Mock()
    topic = SimpleNamespace(
        creator="allo1sender",
        metadata="old",
        loss_method="mse",
        alpha_regret="0.1",
        merit_sortition_alpha="0.1",
        p_norm="3.0",
        c_norm="0.75",
        max_labels_per_submission=1,
        label_whitelist=[],
        label_default_value="0",
        max_top_inferers_to_reward=0,
    )
    query.get_topic = AsyncMock(return_value=SimpleNamespace(topic=topic))
    query.get_params = AsyncMock(
        return_value=SimpleNamespace(
            params=SimpleNamespace(
                min_top_inferers_to_reward=0,
                max_top_inferers_to_reward=32,
            )
        )
    )
    txs = _make_txs(query=query)

    await txs.update_topic(topic_id=3)
    msg = txs._txs.submit_transaction.await_args.kwargs["msgs"][0]
    assert msg.max_top_inferers_to_reward == 0


@pytest.mark.asyncio
async def test_update_topic_rejects_non_creator():
    query = Mock()
    topic = SimpleNamespace(
        creator="allo1other",
        metadata="old",
        loss_method="mse",
        alpha_regret="0.1",
        merit_sortition_alpha="0.1",
        p_norm="3.0",
        c_norm="0.75",
        max_labels_per_submission=1,
        label_whitelist=[],
        label_default_value="0",
        max_top_inferers_to_reward=10,
    )
    query.get_topic = AsyncMock(return_value=SimpleNamespace(topic=topic))
    txs = _make_txs(query=query)
    with pytest.raises(ValueError, match="not permitted to update"):
        await txs.update_topic(topic_id=3, max_top_inferers_to_reward=12)
    txs._txs.submit_transaction.assert_not_called()


# ---------------------------------------------------------------------------
# simulation guard
# ---------------------------------------------------------------------------


def _make_manager() -> TxManager:
    wallet = Mock()
    wallet.address.return_value = "allo1sender"
    wallet.public_key.return_value = Mock()
    return TxManager(
        wallet=wallet,
        tx_client=Mock(),
        auth_client=Mock(),
        bank_client=Mock(),
        feemarket_client=Mock(),
        config=AlloraNetworkConfig.testnet(),
    )


@pytest.mark.asyncio
async def test_submit_transaction_raises_on_simulation_tx_error():
    manager = _make_manager()
    manager.simulate_transaction = AsyncMock(
        side_effect=TxError(codespace="emissions", code=104, message="not permitted")
    )
    manager._attempt_submissions = AsyncMock(return_value=None)

    with pytest.raises(TxError) as exc:
        await manager.submit_transaction(
            type_url="/emissions.v10.InsertWorkerPayloadRequest",
            msgs=[Mock()],
            fee_tier=FeeTier.STANDARD,
        )
    assert exc.value.code == 104
    manager._attempt_submissions.assert_not_called()


@pytest.mark.asyncio
async def test_submit_transaction_falls_back_on_simulation_out_of_gas(
    monkeypatch: pytest.MonkeyPatch,
):
    import asyncio

    manager = _make_manager()
    manager.simulate_transaction = AsyncMock(
        side_effect=OutOfGasError("sim OOG")
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


# ---------------------------------------------------------------------------
# actor preflight wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inferer_preflight_calls_inference_query():
    from allora_sdk.worker.inferer import Inferer

    client = Mock()
    client.emissions = Mock()
    client.emissions.query = Mock()
    with patch(
        "allora_sdk.worker.inferer.can_submit_inference",
        new_callable=AsyncMock,
        return_value=True,
    ) as mocked:
        inferer = Inferer(
            wallet=Mock(address=Mock(return_value="allo1i")),
            client=client,
            topic_id=1,
            run=lambda _: "1.0",
            fee_tier=FeeTier.STANDARD,
        )
        assert await inferer.worker_is_whitelisted() is True
        mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_forecaster_preflight_calls_forecast_query():
    from allora_sdk.worker.forecaster import Forecaster

    client = Mock()
    client.emissions = Mock()
    client.emissions.query = Mock()
    with patch(
        "allora_sdk.worker.forecaster.can_submit_forecast",
        new_callable=AsyncMock,
        return_value=False,
    ) as mocked:
        forecaster = Forecaster(
            wallet=Mock(address=Mock(return_value="allo1f")),
            client=client,
            topic_id=1,
            run=lambda _: {"allo1a": 1.0},
            fee_tier=FeeTier.STANDARD,
        )
        assert await forecaster.worker_is_whitelisted() is False
        mocked.assert_awaited_once()
