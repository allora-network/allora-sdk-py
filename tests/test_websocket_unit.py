#!/usr/bin/env python3
"""
Focused unit tests for websocket utilities: EventFilter, EventMarshaler, and EventRegistry.
These tests are fast and offline.
"""

import json
from typing import Any, Dict

import pytest

from allora_sdk.protobuf_client.client_websocket_events import (
    EventFilter,
    EventMarshaler,
    EventRegistry,
)
from allora_sdk.protobuf_client.proto.emissions.v9 import EventWorkerLastCommitSet


def test_event_filter_query_building():
    q = (
        EventFilter()
        .event_type("NewBlockEvents")
        .custom("emissions.v9.EventWorkerLastCommitSet.topic_id CONTAINS '13'")
        .sender("allo1abc")
        .to_query()
    )
    assert "tm.event='NewBlockEvents'" in q
    assert "emissions.v9.EventWorkerLastCommitSet.topic_id CONTAINS '13'" in q
    assert "message.sender='allo1abc'" in q


def test_event_registry_contains_known_event():
    reg = EventRegistry()
    assert reg.is_registered("emissions.v9.EventWorkerLastCommitSet")
    assert reg.get_event_class("emissions.v9.EventWorkerLastCommitSet") is EventWorkerLastCommitSet


def test_event_marshaler_attribute_parsing_edge_cases():
    reg = EventRegistry()
    marshaler = EventMarshaler(reg)

    event_json: Dict[str, Any] = {
        "type": "emissions.v9.EventWorkerLastCommitSet",
        "attributes": [
            {"key": "topic_id", "value": '"13"'},
            {"key": "block_height", "value": "5100967"},
            {"key": "some_bool", "value": "true"},
            {"key": "some_float", "value": "1.23"},
            {"key": "some_array", "value": "[1,2,3]"},
            {"key": "some_json", "value": json.dumps({"a": 1})},
        ],
    }

    # Only fields defined on the proto will be used; extras are ignored.
    event = marshaler.marshal_event(event_json)
    assert event is not None
    assert getattr(event, "topic_id") == 13
    assert getattr(event, "block_height") == 5100967


