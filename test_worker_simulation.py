# #!/usr/bin/env python3
# """
# Simulate the exact AlloraWorker subscription process to debug why callbacks fail.
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


# async def test_exact_worker_simulation():
#     """Test the exact subscription process used by AlloraWorker."""
#     print("\n🧪 Testing exact AlloraWorker simulation...")
    
#     # Create WebSocket subscriber
#     mock_client = MockClient()
#     subscriber = AlloraWebsocketSubscriber(mock_client)
    
#     # Mock the WebSocket connection to avoid actual connection
#     subscriber.websocket = MagicMock()
#     subscriber.websocket.close_code = None
#     subscriber.websocket.send = AsyncMock()
    
#     # Track callback execution
#     callback_executed = False
#     received_event = None
#     received_height = None
    
#     async def worker_handle_event(event: EventWorkerLastCommitSet, block_height: Optional[int]):
#         nonlocal callback_executed, received_event, received_height
#         print(f"🔥 WORKER._handle_event CALLED! Event: {event}, Height: {block_height}")
#         callback_executed = True
#         received_event = event
#         received_height = block_height
    
#     print("📝 Calling subscribe_new_block_events_typed (like AlloraWorker does)...")
    
#     # Use the exact same call as AlloraWorker
#     subscription_id = await subscriber.subscribe_new_block_events_typed(
#         EventWorkerLastCommitSet,
#         [EventAttributeCondition("topic_id", "CONTAINS", "13")],
#         worker_handle_event,
#     )
    
#     print(f"✅ Subscription created: {subscription_id}")
#     print(f"📊 Subscription details: {subscriber.subscriptions[subscription_id]}")
#     print(f"📞 Callback details: {subscriber.callbacks[subscription_id]}")
    
#     # Load the captured message
#     with open("tests/fixtures/new_block_events.json", "r") as f:
#         websocket_message = json.load(f)
    
#     captured_message_id = websocket_message.get("id")
#     print(f"🔍 Captured message ID: {captured_message_id}")
#     print(f"🔍 Our subscription ID: {subscription_id}")
    
#     # Check if they match
#     if captured_message_id != subscription_id:
#         print(f"⚠️ MESSAGE ID MISMATCH! This could be the bug!")
#         print(f"   Real worker creates: '{subscription_id}'")
#         print(f"   But message has ID:  '{captured_message_id}'")
        
#         # Update our subscription to use the captured message ID
#         print("🔧 Updating subscription to match captured message ID...")
#         subscriber.subscriptions[captured_message_id] = subscriber.subscriptions.pop(subscription_id)
#         subscriber.callbacks[captured_message_id] = subscriber.callbacks.pop(subscription_id)
#         subscription_id = captured_message_id
    
#     message_json = json.dumps(websocket_message)
    
#     print("📨 Injecting captured message...")
#     await subscriber._handle_message(message_json)
    
#     print("📊 Verifying results...")
    
#     if callback_executed:
#         print(f"✅ SUCCESS! Callback executed:")
#         print(f"   📊 Event topic_id: {received_event.topic_id}")
#         print(f"   📊 Event block_height: {received_event.block_height}")
#         print(f"   📊 Callback block_height: {received_height}")
#         print(f"   📊 Event nonce: {received_event.nonce}")
#         return True
#     else:
#         print("❌ FAILURE! Callback was never executed")
#         return False


# async def main():
#     """Run the worker simulation test."""
#     try:
#         success = await test_exact_worker_simulation()
#         if success:
#             print("\n🎉 Worker simulation PASSED!")
#         else:
#             print("\n💥 Worker simulation FAILED!")
#     except Exception as e:
#         print(f"\n💥 Test FAILED with exception: {e}")
#         import traceback
#         traceback.print_exc()


# if __name__ == "__main__":
#     asyncio.run(main())