"""B1a: actors resolve and cache their own topic, and derive the payload
timeout from it.

Covers the worker/actor boundary introduced in the B1a refactor:
- the topic is fetched once per actor process and cached,
- the timeout height is computed from the cached topic and forwarded to the
  payload submission,
- a failed topic lookup leaves the timeout unset but does not block the
  submission, and is retried on the next submission.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from allora_sdk.rpc_client.tx_manager import FeeTier
from allora_sdk.worker.forecaster import Forecaster
from allora_sdk.worker.inferer import Inferer, SanityCheckConfig
from allora_sdk.worker.reputer import Reputer


def _make_wallet() -> Mock:
    wallet = Mock()
    wallet.address.return_value = "allo1actor"
    return wallet


def _make_client() -> Mock:
    client = Mock()
    client.emissions = Mock()
    client.emissions.tx = Mock()
    client.emissions.query = Mock()
    return client


def _pending() -> Mock:
    pending = Mock()
    pending.wait = AsyncMock(return_value=Mock(txhash="abc123"))
    return pending


def _make_inferer(client: Mock) -> Inferer:
    return Inferer(
        wallet=_make_wallet(),
        client=client,
        topic_id=7,
        run=lambda _ctx: 1.0,
        fee_tier=FeeTier.STANDARD,
        sanity_check=SanityCheckConfig(enabled=False),
    )


@pytest.mark.asyncio
async def test_inferer_caches_topic_and_forwards_timeout() -> None:
    client = _make_client()
    topic = Mock(worker_submission_window=5)
    client.emissions.query.get_topic = AsyncMock(return_value=Mock(topic=topic))
    client.emissions.tx.insert_worker_payload = AsyncMock(return_value=_pending())

    inferer = _make_inferer(client)

    await inferer.submit(nonce=100, account_seq=1)
    first = client.emissions.tx.insert_worker_payload.await_args.kwargs
    assert first["timeout_height"] == 105

    await inferer.submit(nonce=101, account_seq=2)
    second = client.emissions.tx.insert_worker_payload.await_args.kwargs
    assert second["timeout_height"] == 106

    # Cached: the topic is fetched once, not per submission.
    assert client.emissions.query.get_topic.await_count == 1


@pytest.mark.asyncio
async def test_inferer_retries_topic_resolution_after_failure() -> None:
    client = _make_client()
    topic = Mock(worker_submission_window=5)
    client.emissions.query.get_topic = AsyncMock(
        side_effect=[RuntimeError("chain blip"), Mock(topic=topic)]
    )
    client.emissions.tx.insert_worker_payload = AsyncMock(return_value=_pending())

    inferer = _make_inferer(client)

    # A failed lookup must not block submission; it just leaves expiry unset.
    await inferer.submit(nonce=100, account_seq=1)
    assert client.emissions.tx.insert_worker_payload.await_args.kwargs["timeout_height"] is None

    # The next submission retries the lookup and then forwards a timeout.
    await inferer.submit(nonce=101, account_seq=2)
    assert client.emissions.tx.insert_worker_payload.await_args.kwargs["timeout_height"] == 106
    assert client.emissions.query.get_topic.await_count == 2


@pytest.mark.asyncio
async def test_forecaster_forwards_timeout_from_cached_topic() -> None:
    client = _make_client()
    topic = Mock(worker_submission_window=3)
    client.emissions.query.get_topic = AsyncMock(return_value=Mock(topic=topic))
    client.emissions.tx.insert_worker_payload = AsyncMock(return_value=_pending())

    forecaster = Forecaster(
        wallet=_make_wallet(),
        client=client,
        topic_id=7,
        run=lambda _ctx: {"allo1a": 1.0},
        fee_tier=FeeTier.STANDARD,
    )

    await forecaster.submit(nonce=50, account_seq=0)
    assert client.emissions.tx.insert_worker_payload.await_args.kwargs["timeout_height"] == 53

    await forecaster.submit(nonce=51, account_seq=1)
    assert client.emissions.tx.insert_worker_payload.await_args.kwargs["timeout_height"] == 54
    assert client.emissions.query.get_topic.await_count == 1


@pytest.mark.asyncio
async def test_reputer_caches_topic_and_forwards_reputer_timeout() -> None:
    client = _make_client()
    topic = Mock(ground_truth_lag=50, epoch_length=36)
    client.emissions.query.get_topic = AsyncMock(return_value=Mock(topic=topic))
    client.emissions.query.get_network_inferences_at_block = AsyncMock(
        return_value=Mock(network_inferences=Mock())
    )
    client.emissions.tx.insert_reputer_payload = AsyncMock(return_value=_pending())

    reputer = Reputer(
        wallet=_make_wallet(),
        client=client,
        topic_id=9,
        reputer_fn=lambda _ctx, _value: 1.0,
        fee_tier=FeeTier.STANDARD,
    )
    reputer._compute_loss_bundle = AsyncMock(return_value=Mock())
    reputer._maybe_stake = AsyncMock(return_value=None)

    await reputer.submit(nonce=1000, account_seq=4)
    assert client.emissions.tx.insert_reputer_payload.await_args.kwargs["timeout_height"] == 1108

    await reputer.submit(nonce=1001, account_seq=5)
    assert client.emissions.tx.insert_reputer_payload.await_args.kwargs["timeout_height"] == 1109
    assert client.emissions.query.get_topic.await_count == 1
