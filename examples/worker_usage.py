#!/usr/bin/env python3
"""
Example usage patterns for AlloraWorker in different environments.

This demonstrates the ML-friendly async generator interface for submitting
predictions to the Allora network across shell, Jupyter, and CoLab environments.
"""

import asyncio
import random
from allora_sdk.worker.worker import AlloraWorker
from allora_sdk.protobuf_client.tx_manager import FeeTier

# Example prediction function - replace with your ML model
def simple_prediction_model():
    """Example prediction function that returns a random price."""
    return round(random.uniform(2000, 3000), 2)

async def shell_usage():
    """Example usage in shell/script environment with automatic signal handling."""
    print("🔧 Shell/Script Usage Example")
    
    worker = AlloraWorker(
        topic_id=13,
        predict_fn=simple_prediction_model,
        fee_tier=FeeTier.STANDARD
    )
    
    try:
        # This will run indefinitely until Ctrl+C
        async for result in worker.run():
            print(f"✅ {result}")
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user")

async def notebook_usage():
    """Example usage in Jupyter/CoLab with timeout and manual control."""
    print("📓 Notebook Usage Example")
    
    worker = AlloraWorker(
        topic_id=13,
        predict_fn=simple_prediction_model,
        fee_tier=FeeTier.PRIORITY
    )
    
    # Run for 5 minutes max (good for notebook demos)
    try:
        async for result in worker.run(timeout=300):
            print(f"✅ {result}")
    except asyncio.TimeoutError:
        print("⏰ Stopped after timeout")
    
    # Or with manual control:
    # worker = AlloraWorker(...)
    # task = asyncio.create_task(worker_loop(worker))
    # await asyncio.sleep(60)  # Run for 1 minute
    # worker.stop()
    # await task

async def context_manager_usage():
    """Example usage with async context manager for guaranteed cleanup."""
    print("🔒 Context Manager Usage Example")
    
    async with AlloraWorker(
        topic_id=13,
        predict_fn=simple_prediction_model,
        fee_tier=FeeTier.ECO
    ) as worker:
        count = 0
        async for result in worker.run():
            print(f"✅ {result}")
            count += 1
            if count >= 3:  # Stop after 3 predictions
                break
    # Automatic cleanup happens here

async def custom_model_usage():
    """Example with a more realistic ML model setup."""
    print("🤖 Custom ML Model Usage Example")
    
    class SimpleMLModel:
        def __init__(self):
            self.last_prediction = 2500.0
            
        def predict(self):
            # Simple random walk model
            change = random.uniform(-50, 50)
            self.last_prediction += change
            return round(max(self.last_prediction, 100), 2)  # Don't go below $100
    
    model = SimpleMLModel()
    
    worker = AlloraWorker(
        topic_id=13,
        predict_fn=model.predict,
        mnemonic="your-mnemonic-here",  # Use your own mnemonic
        fee_tier=FeeTier.STANDARD
    )
    
    try:
        async for result in worker.run(timeout=120):  # 2 minute demo
            print(f"🎯 Model prediction: {result}")
    except KeyboardInterrupt:
        print("\n🛑 Demo stopped")

async def error_handling_example():
    """Example showing error handling and recovery."""
    print("⚠️ Error Handling Example")
    
    def sometimes_failing_model():
        if random.random() < 0.3:  # 30% chance of failure
            raise ValueError("Model prediction failed!")
        return round(random.uniform(2000, 3000), 2)
    
    worker = AlloraWorker(
        topic_id=13,
        predict_fn=sometimes_failing_model,
        fee_tier=FeeTier.STANDARD
    )
    
    prediction_count = 0
    try:
        async for result in worker.run():
            print(f"✅ Success: {result}")
            prediction_count += 1
            if prediction_count >= 5:  # Stop after 5 successful predictions
                worker.stop()
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
    
    print(f"📊 Completed {prediction_count} predictions")

if __name__ == "__main__":
    import sys
    
    # Detect environment and run appropriate example
    if len(sys.argv) > 1:
        example = sys.argv[1]
    else:
        example = "notebook"  # Default to notebook example for safety
    
    examples = {
        "shell": shell_usage,
        "notebook": notebook_usage,
        "context": context_manager_usage,
        "model": custom_model_usage,
        "errors": error_handling_example,
    }
    
    if example not in examples:
        print(f"Available examples: {', '.join(examples.keys())}")
        sys.exit(1)
    
    print(f"🚀 Running {example} example...\n")
    
    try:
        asyncio.run(examples[example]())
    except KeyboardInterrupt:
        print("\n👋 Example stopped by user")
    
    print("\n✨ Example completed!")