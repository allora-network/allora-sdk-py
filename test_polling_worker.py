#!/usr/bin/env python3

################################################################################
# TEMPORARY POLLING-BASED WORKER TEST - REMOVE WHEN EVENT SUBSCRIPTIONS WORK
# This is a workaround test until WebSocket event listening works properly
# TODO: Replace with proper event-based worker test when WebSocket is fixed
################################################################################

"""
Simple test for the polling-based Allora worker.
This worker polls for unfulfilled nonces and submits predictions immediately.
"""

import asyncio
import logging
import random
from allora_sdk import AlloraWorker

# Set up logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def my_predict() -> str:
    """Simple prediction function that returns a random price prediction."""
    prediction = 50000 + random.uniform(-5000, 5000)  # BTC price around $50k ± $5k
    return str(round(prediction, 2))

async def main():
    """Run a simple polling-based worker for topic 13."""
    print("🚀 Starting polling-based Allora worker...")
    
    # Create worker (uses environment variables for mnemonic/config)
    worker = AlloraWorker(topic_id=13, predict_fn=my_predict)
    
    try:
        print("🔄 Starting polling loop...")
        async for result in worker.run():
            print(f"📊 {result}")
    except KeyboardInterrupt:
        print("\n🛑 Worker stopped by user")
    except Exception as e:
        print(f"❌ Worker error: {e}")

if __name__ == "__main__":
    asyncio.run(main())