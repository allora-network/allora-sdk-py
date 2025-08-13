# #!/usr/bin/env python3
# """
# Simple callback execution test without pytest.
# """

# import json
# import asyncio
# from typing import Any, Optional
# from unittest.mock import MagicMock, AsyncMock

# from allora_sdk.protobuf_client.client_websocket_events import AlloraWebsocketSubscriber, EventAttributeCondition
# from allora_sdk.protobuf_client.proto.emissions.v9 import EventWorkerLastCommitSet


# class MockClient:
#     """Mock client for testing WebSocket subscriber."""
    
#     def __init__(self):
#         self.config = MagicMock()
#         self.config.websocket_endpoint = "ws://localhost:26657/websocket"


# async def test_callback_execution():
#     """Test callback execution with captured WebSocket data."""
#     print("\n🧪 Testing direct message injection for callback execution...")
    
#     # Load captured WebSocket message
#     with open("tests/fixtures/new_block_events.json", "r") as f:
#         websocket_message = json.load(f)
    
#     print(f"📄 Loaded captured message: {len(json.dumps(websocket_message))} bytes")
    
#     # Create WebSocket subscriber
#     mock_client = MockClient()
#     subscriber = AlloraWebsocketSubscriber(mock_client)
    
#     # Create callback tracking
#     callback_executed = False
#     received_event = None
#     received_height = None
    
#     async def test_callback(event: EventWorkerLastCommitSet, block_height: Optional[int]):
#         nonlocal callback_executed, received_event, received_height
#         print(f"🔥 CALLBACK EXECUTED! Event: {event}, Height: {block_height}")
#         callback_executed = True
#         received_event = event
#         received_height = block_height
    
#     print("📝 Setting up typed subscription...")
    
#     # Set up subscription manually (simulating what the worker does)
#     # IMPORTANT: Use the same subscription ID as in the captured message!
#     subscription_id = "typed_block_events_1"
#     event_name = "emissions.v9.EventWorkerLastCommitSet"
    
#     # Store subscription info
#     subscriber.subscriptions[subscription_id] = {
#         "query": "tm.event='NewBlockEvents' AND emissions.v9.EventWorkerLastCommitSet.topic_id CONTAINS '13'",
#         "event_name": event_name,
#         "event_class": EventWorkerLastCommitSet,
#         "event_attribute_conditions": [EventAttributeCondition("topic_id", "CONTAINS", "13")],
#         "active": True,
#         "subscription_type": "TypedNewBlockEvents"
#     }
    
#     # Store callback
#     subscriber.callbacks[subscription_id] = [test_callback]
    
#     print("✅ Subscription setup complete")
#     print("📨 Injecting captured message...")
    
#     # Convert the captured message back to JSON string for processing
#     message_json = json.dumps(websocket_message)
    
#     print(f"🔍 Message ID from captured data: {websocket_message.get('id')}")
#     print(f"🔍 Our subscription ID: {subscription_id}")
    
#     # Directly call _handle_message with captured data
#     await subscriber._handle_message(message_json)
    
#     print(f"🔍 Subscription still active: {subscriber.subscriptions.get(subscription_id, {}).get('active')}")
#     print(f"🔍 Callbacks still registered: {len(subscriber.callbacks.get(subscription_id, []))}")
    
#     print("📊 Verifying results...")
    
#     # Verify callback was executed
#     if not callback_executed:
#         print("❌ CALLBACK WAS NEVER EXECUTED!")
#         return False
    
#     if received_event is None:
#         print("❌ NO EVENT RECEIVED IN CALLBACK!")
#         return False
    
#     if received_height != 5100967:
#         print(f"❌ WRONG BLOCK HEIGHT: expected 5100967, got {received_height}")
#         return False
    
#     # Verify event contents
#     if received_event.topic_id != 13:
#         print(f"❌ WRONG TOPIC_ID: expected 13, got {received_event.topic_id}")
#         return False
        
#     if received_event.block_height != 5100967:
#         print(f"❌ WRONG BLOCK_HEIGHT: expected 5100967, got {received_event.block_height}")
#         return False
    
#     print(f"✅ Callback executed successfully!")
#     print(f"   📊 Event topic_id: {received_event.topic_id}")
#     print(f"   📊 Event block_height: {received_event.block_height}")
#     print(f"   📊 Callback block_height: {received_height}")
#     print(f"   📊 Event nonce: {received_event.nonce}")
#     return True


# async def main():
#     """Run the callback execution test."""
#     try:
#         success = await test_callback_execution()
#         if success:
#             print("\n🎉 Test PASSED - Callback execution works correctly!")
#         else:
#             print("\n💥 Test FAILED - Callback execution has issues!")
#     except Exception as e:
#         print(f"\n💥 Test FAILED with exception: {e}")
#         import traceback
#         traceback.print_exc()


# if __name__ == "__main__":
#     asyncio.run(main())