#!/usr/bin/env python3
import asyncio
from typing import Any

import pytest

from allora_sdk.worker import AlloraWorker
from allora_sdk.protobuf_client.tx_manager import FeeTier
from allora_sdk.protobuf_client.proto.emissions.v3 import Nonce as V3Nonce
from allora_sdk.protobuf_client.proto.emissions.v9 import EventWorkerLastCommitSet
from tests.mocks.client import (
    DummyWallet,
    MockEmissions,
    MockEvents,
    MockProtobufClient,
)
from tests.utils.crypto import gen_private_key_hex


@pytest.mark.asyncio
async def test_worker_successful_submission(monkeypatch):
    # Arrange: patch client factory to return our mock
    mock_client = MockProtobufClient()
    monkeypatch.setattr("allora_sdk.worker.worker.ProtobufClient", MockProtobufClient)

    # Predict function
    def predict() -> float:
        return 123.45

    worker = AlloraWorker(
        topic_id=13,
        predict_fn=predict,
        fee_tier=FeeTier.PRIORITY,
        private_key=gen_private_key_hex(),
        debug=True,
    )

    # Configure emissions behavior
    height = 5100967
    mock_client = worker.client  # type: ignore[attr-defined]
    mock_client.emissions.set_can_submit(True)
    mock_client.emissions.set_unfulfilled([height])

    async def get_first_result():
        async for msg in worker.run():
            return msg

    # Start the worker
    task = asyncio.create_task(get_first_result())

    # Wait for subscription and emit event
    await mock_client.events.wait_for_subscribed()
    evt = EventWorkerLastCommitSet(topic_id=13, block_height=height, nonce=V3Nonce(block_height=height))
    await mock_client.events.emit(evt, height)

    # Assert the yielded result
    result = await asyncio.wait_for(task, timeout=3)
    assert result is True

    # Assert insert payload called with correct args
    assert mock_client.emissions.insert_calls, "insert_worker_payload was not called"
    topic_id, value, fee = mock_client.emissions.insert_calls[0]
    assert topic_id == 13
    assert value == "123.45"
    assert fee == FeeTier.PRIORITY

    # Cleanup
    worker.stop()
    await asyncio.sleep(0.05)
    # Unsubscribe should be invoked during cleanup when the async gen finalizes
    assert mock_client.events.unsubscribe_calls == ["sub-1"]


@pytest.mark.asyncio
async def test_worker_cannot_submit(monkeypatch):
    mock_client = MockProtobufClient()
    monkeypatch.setattr("allora_sdk.worker.worker.ProtobufClient", MockProtobufClient)

    def predict() -> float:
        return 88.0

    worker = AlloraWorker(topic_id=13, predict_fn=predict, private_key=gen_private_key_hex())

    # Configure emissions behavior: cannot submit
    height = 42
    mock_client = worker.client  # type: ignore[attr-defined]
    mock_client.emissions.set_can_submit(False)
    mock_client.emissions.set_unfulfilled([height])

    results: list[Any] = []
    async def collect():
        async for msg in worker.run(timeout=0.2):
            results.append(msg)

    task = asyncio.create_task(collect())
    await mock_client.events.wait_for_subscribed()
    evt = EventWorkerLastCommitSet(topic_id=13, block_height=height, nonce=V3Nonce(block_height=height))
    await mock_client.events.emit(evt, height)

    await task

    # Ensure no insert
    assert not mock_client.emissions.insert_calls
    assert results == []

    # Cleanup
    worker.stop()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_worker_nonce_already_fulfilled(monkeypatch):
    monkeypatch.setattr("allora_sdk.worker.worker.ProtobufClient", MockProtobufClient)

    def predict() -> float:
        return 77.0

    worker = AlloraWorker(topic_id=13, predict_fn=predict, private_key=gen_private_key_hex())

    height = 100
    mock_client = worker.client  # type: ignore[attr-defined]
    mock_client.emissions.set_can_submit(True)
    mock_client.emissions.set_unfulfilled([])  # already fulfilled

    results: list[Any] = []
    async def collect():
        async for msg in worker.run(timeout=0.2):
            results.append(msg)

    task = asyncio.create_task(collect())
    await mock_client.events.wait_for_subscribed()
    evt = EventWorkerLastCommitSet(topic_id=13, block_height=height, nonce=V3Nonce(block_height=height))
    await mock_client.events.emit(evt, height)

    await task

    assert not mock_client.emissions.insert_calls
    assert results == []
    worker.stop()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_worker_predict_fn_error(monkeypatch):
    monkeypatch.setattr("allora_sdk.worker.worker.ProtobufClient", MockProtobufClient)

    def predict() -> float:
        raise RuntimeError("model failed")

    worker = AlloraWorker(topic_id=13, predict_fn=predict, private_key=gen_private_key_hex())

    height = 7
    mock_client = worker.client  # type: ignore[attr-defined]
    mock_client.emissions.set_can_submit(True)
    mock_client.emissions.set_unfulfilled([height])

    results: list[Any] = []
    async def collect():
        async for msg in worker.run(timeout=0.2):
            results.append(msg)

    task = asyncio.create_task(collect())
    await mock_client.events.wait_for_subscribed()
    evt = EventWorkerLastCommitSet(topic_id=13, block_height=height, nonce=V3Nonce(block_height=height))
    await mock_client.events.emit(evt, height)

    await task

    assert not mock_client.emissions.insert_calls
    assert results == []
    worker.stop()
    await asyncio.sleep(0.05)


