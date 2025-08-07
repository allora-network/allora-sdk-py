#!/usr/bin/env python3
"""
Unit tests for typed event subscriptions
"""

import asyncio
import json
from unittest.mock import Mock, AsyncMock
from allora_sdk.protobuf_client.events import (
    AlloraWebsocketSubscriber, 
    EventAttributeCondition,
    EventRegistry,
    EventMarshaler
)
from allora_sdk.protobuf_client.proto.emissions.v9 import EventScoresSet

# Mock WebSocket message data
MOCK_WEBSOCKET_MESSAGE = {
    "jsonrpc": "2.0",
    "id": "block_events_1", 
    "result": {
        "query": "tm.event='NewBlockEvents'",
        "data": {
            "type": "tendermint/event/NewBlockEvents",
            "value": {
                "height": "5058055",
                "events": [
                    {
                        "type": "coin_spent",
                        "attributes": [
                            {"key": "spender", "value": "\"allo123\"", "index": True}
                        ]
                    },
                    {
                        "type": "emissions.v9.EventScoresSet",
                        "attributes": [
                            {"key": "actor_type", "value": "\"ACTOR_TYPE_REPUTER\"", "index": True},
                            {"key": "addresses", "value": "[\"allo1gs85hz7svvp6f9hrwle43lknaqnaxq6885s9ps\"]", "index": True},
                            {"key": "block_height", "value": "\"5058055\"", "index": True},
                            {"key": "scores", "value": "[\"99.99999999999999999999999999999754\"]", "index": True},
                            {"key": "topic_id", "value": "\"13\"", "index": True},
                            {"key": "mode", "value": "EndBlock", "index": True}
                        ]
                    },
                    {
                        "type": "transfer",
                        "attributes": [
                            {"key": "recipient", "value": "\"allo456\"", "index": True}
                        ]
                    }
                ]
            }
        }
    }
}

class TestTypedSubscriptions:
    
    def setup_method(self):
        """Setup for each test."""
        # Create mock client
        self.mock_client = Mock()
        self.mock_client.config.websocket_endpoint = "ws://mock-endpoint"
        
        # Create subscriber
        self.subscriber = AlloraWebsocketSubscriber(self.mock_client)
        
        # Mock WebSocket connection
        self.mock_websocket = AsyncMock()
        self.subscriber.websocket = self.mock_websocket
        
        # Track callback calls
        self.generic_callback_calls = []
        self.typed_callback_calls = []
        
    def generic_callback(self, events):
        """Mock generic callback."""
        print(f"🔴 GENERIC CALLBACK: {len(events)} events")
        self.generic_callback_calls.append(events)
        
    def typed_callback(self, events):
        """Mock typed callback."""
        print(f"🟢 TYPED CALLBACK: {len(events)} events")
        self.typed_callback_calls.append(events)
        for event in events:
            print(f"   Event type: {type(event)}")
            if hasattr(event, 'topic_id'):
                print(f"   Topic ID: {event.topic_id}")
    
    async def test_event_registry_discovery(self):
        """Test that EventRegistry discovers EventScoresSet."""
        registry = EventRegistry()
        
        print(f"📊 Registry discovered {len(registry._event_map)} event types")
        
        # Check if EventScoresSet is registered
        event_class = registry.get_event_class("emissions.v9.EventScoresSet")
        print(f"🔍 EventScoresSet lookup: {event_class}")
        
        assert event_class is not None, "EventScoresSet should be registered"
        assert event_class == EventScoresSet, "Should map to correct class"
        print("✅ Event registry test passed")
    
    async def test_event_marshaling(self):
        """Test JSON to protobuf marshaling."""
        registry = EventRegistry()
        marshaler = EventMarshaler(registry)
        
        # Test event from mock data
        test_event = MOCK_WEBSOCKET_MESSAGE["result"]["data"]["value"]["events"][1]
        print(f"🧪 Testing marshaling for: {test_event['type']}")
        
        # Marshal the event
        protobuf_event = marshaler.marshal_event(test_event)
        print(f"🔄 Marshaled result: {protobuf_event}")
        print(f"🔍 Result type: {type(protobuf_event)}")
        
        assert protobuf_event is not None, "Should successfully marshal event"
        assert isinstance(protobuf_event, EventScoresSet), "Should be EventScoresSet instance"
        assert protobuf_event.topic_id == 13, "Should parse topic_id correctly"
        
        print("✅ Event marshaling test passed")
        
    async def test_subscription_creation(self):
        """Test that both generic and typed subscriptions are created."""
        conditions = [EventAttributeCondition("topic_id", "CONTAINS", "13")]
        
        # Create generic subscription
        generic_id = await self.subscriber.subscribe_new_block_events(
            "emissions.v9.EventScoresSet",
            conditions,
            self.generic_callback
        )
        
        # Create typed subscription  
        typed_id = await self.subscriber.subscribe_new_block_events_typed(
            EventScoresSet,
            conditions,
            self.typed_callback
        )
        
        print(f"📋 Created subscriptions: {generic_id}, {typed_id}")
        print(f"📊 Total subscriptions: {len(self.subscriber.subscriptions)}")
        print(f"📞 Total callbacks: {len(self.subscriber.callbacks)}")
        
        # Verify subscriptions exist
        assert generic_id in self.subscriber.subscriptions
        assert typed_id in self.subscriber.subscriptions
        assert generic_id in self.subscriber.callbacks
        assert typed_id in self.subscriber.callbacks
        
        # Verify subscription details
        generic_sub = self.subscriber.subscriptions[generic_id]
        typed_sub = self.subscriber.subscriptions[typed_id]
        
        print(f"🔍 Generic sub type: {generic_sub['subscription_type']}")
        print(f"🔍 Typed sub type: {typed_sub['subscription_type']}")
        print(f"🔍 Generic query: {generic_sub['query']}")
        print(f"🔍 Typed query: {typed_sub['query']}")
        print(f"🔍 Queries match: {generic_sub['query'] == typed_sub['query']}")
        
        assert generic_sub["subscription_type"] == "NewBlockEvents"
        assert typed_sub["subscription_type"] == "TypedNewBlockEvents"
        assert generic_sub["query"] == typed_sub["query"], "Queries should be identical"
        
        print("✅ Subscription creation test passed")
        return generic_id, typed_id
    
    async def test_event_dispatching(self):
        """Test that events are dispatched to both subscriptions."""
        generic_id, typed_id = await self.test_subscription_creation()
        
        # Mark subscriptions as active (simulate confirmation)
        self.subscriber.subscriptions[generic_id]["active"] = True
        self.subscriber.subscriptions[typed_id]["active"] = True
        
        # Simulate WebSocket message handling
        message = json.dumps(MOCK_WEBSOCKET_MESSAGE)
        print(f"🧪 Simulating WebSocket message processing...")
        
        await self.subscriber._handle_message(message)
        
        print(f"📊 Generic callback calls: {len(self.generic_callback_calls)}")
        print(f"📊 Typed callback calls: {len(self.typed_callback_calls)}")
        
        # Check that both callbacks were called
        assert len(self.generic_callback_calls) > 0, "Generic callback should be called"
        assert len(self.typed_callback_calls) > 0, "Typed callback should be called"
        
        # Check the callback content
        generic_events = self.generic_callback_calls[0]
        typed_events = self.typed_callback_calls[0]
        
        print(f"🔍 Generic events count: {len(generic_events)}")
        print(f"🔍 Typed events count: {len(typed_events)}")
        
        assert len(generic_events) == 1, "Should have 1 matching event"
        assert len(typed_events) == 1, "Should have 1 matching event"
        
        # Verify event types
        generic_event = generic_events[0]
        typed_event = typed_events[0]
        
        assert generic_event["type"] == "emissions.v9.EventScoresSet"
        assert isinstance(typed_event, EventScoresSet)
        assert typed_event.topic_id == 13
        
        print("✅ Event dispatching test passed")

async def run_tests():
    """Run all tests."""
    test = TestTypedSubscriptions()
    
    print("🧪 Running Event Registry Test...")
    test.setup_method()
    await test.test_event_registry_discovery()
    
    print("\n🧪 Running Event Marshaling Test...")
    test.setup_method()
    await test.test_event_marshaling()
    
    print("\n🧪 Running Subscription Creation Test...")
    test.setup_method()
    await test.test_subscription_creation()
    
    print("\n🧪 Running Event Dispatching Test...")
    test.setup_method()
    await test.test_event_dispatching()
    
    print("\n🎉 All tests completed!")

if __name__ == "__main__":
    asyncio.run(run_tests())