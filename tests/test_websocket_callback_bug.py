#!/usr/bin/env python3
"""
Direct WebSocket message injection test for debugging callback execution.

This test bypasses the WebSocket connection and directly injects the captured
message to test the event processing pipeline and identify callback execution bugs.
"""

import pytest
import json
import asyncio
from typing import Optional
from unittest.mock import MagicMock

from allora_sdk.protobuf_client.client_websocket_events import AlloraWebsocketSubscriber, EventAttributeCondition
from allora_sdk.protobuf_client.proto.emissions.v9 import EventWorkerLastCommitSet
from allora_sdk.protobuf_client.client import ProtobufClient
from tests.mocks.websocket import MockWebSocket, mock_connect


class MockClient:
    """Mock client for testing WebSocket subscriber."""
    
    def __init__(self):
        self.config = MagicMock()
        self.config.websocket_endpoint = "ws://localhost:26657/websocket"


def fake_connect(url: str):
    return MockWebSocket


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_direct_message_injection_callback_execution():
    """
    Test direct message injection to debug callback execution.
    
    This test directly calls _handle_message() with captured WebSocket data
    to verify that callbacks are properly executed for EventWorkerLastCommitSet events.
    """
    with open("./tests/fixtures/new_block_events.json", "r") as f:
        websocket_message = json.load(f)

    mock_client = MockClient()
    subscriber = AlloraWebsocketSubscriber("", connect_fn=mock_connect)
    
    callback_executed = asyncio.Event()
    received_event = None
    received_height = None
    
    async def test_callback(event: EventWorkerLastCommitSet, block_height: Optional[int]):
        nonlocal callback_executed, received_event, received_height
        callback_executed.set()
        received_event = event
        received_height = block_height
    
    subscription_id = "test_subscription"
    event_name = "emissions.v9.EventWorkerLastCommitSet"
    
    subscriber.subscriptions[subscription_id] = {
        "query": "tm.event='NewBlockEvents' AND emissions.v9.EventWorkerLastCommitSet.topic_id CONTAINS '13'",
        "event_name": event_name,
        "event_class": EventWorkerLastCommitSet,
        "event_attribute_conditions": [EventAttributeCondition("topic_id", "CONTAINS", "13")],
        "active": True,
        "subscription_type": "TypedNewBlockEvents"
    }
    
    subscriber.callbacks[subscription_id] = [test_callback]
    
    # Ensure the message routes to our subscription
    websocket_message["id"] = subscription_id
    message_json = json.dumps(websocket_message)

    await subscriber._handle_message(message_json)

    await asyncio.wait_for(callback_executed.wait(), timeout=2)
    assert received_event is not None, "No event received in callback!"
    assert received_height == 5100967, f"Wrong block height: expected 5100967, got {received_height}"
    assert received_event.topic_id == 13, f"Wrong topic_id: expected 13, got {received_event.topic_id}"
    assert received_event.block_height == 5100967, f"Wrong block_height: expected 5100967, got {received_event.block_height}"

    # Ensure nested nonce is a typed message, not a dict
    assert hasattr(received_event, "nonce"), "Event missing nonce"
    assert hasattr(received_event.nonce, "block_height"), "Nonce should be a typed message with block_height"
    assert received_event.nonce.block_height == 5100955


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_subscribe_flow_end_to_end_with_mocked_websocket(monkeypatch):
    """
    High-level: patch the websocket connector, call subscribe, and let the
    background event loop read from a mocked websocket's recv().
    """
    mock_client = MockClient()

    # Patch the connector
    monkeypatch.setattr(
        "allora_sdk.protobuf_client.client_websocket_events.websockets.connect",
        mock_connect,
    )

    subscriber = AlloraWebsocketSubscriber("", connect_fn=mock_connect)

    callback_executed = asyncio.Event()
    received_event = None

    def worker_callback(event: EventWorkerLastCommitSet, block_height: Optional[int]):
        nonlocal received_event
        received_event = event
        callback_executed.set()

    subscription_id = await subscriber.subscribe_new_block_events_typed(
        EventWorkerLastCommitSet,
        [EventAttributeCondition("topic_id", "CONTAINS", "13")],
        worker_callback,
    )

    # A subscribe payload should have been sent
    assert isinstance(subscriber.websocket, MockWebSocket)
    assert subscriber.websocket.sent_payloads, "No payloads sent over websocket"
    subscribe_payload = json.loads(subscriber.websocket.sent_payloads[0])
    assert subscribe_payload.get("method") == "subscribe"
    assert subscribe_payload.get("id") == subscription_id
    assert subscribe_payload.get("params", {}).get("query")

    # Now enqueue a NewBlockEvents message targeting our subscription
    with open("./tests/fixtures/new_block_events.json", "r") as f:
        websocket_message = json.load(f)
    websocket_message["id"] = subscription_id
    await subscriber.websocket.enqueue(json.dumps(websocket_message))

    await asyncio.wait_for(callback_executed.wait(), timeout=3)
    assert received_event is not None, "No event received in worker callback!"
    assert received_event.topic_id == 13, f"Wrong topic_id: {received_event.topic_id}"

    await subscriber.stop()

