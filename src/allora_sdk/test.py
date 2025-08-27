#!/usr/bin/env python3
"""
Simple AlloraWorker test - exactly what an ML engineer would type in Jupyter.
"""

import asyncio
from allora_sdk.worker import AlloraWorker

def my_model():
    """Your ML model goes here."""
    import random
    return round(116000 + random.uniform(-5000, 5000), 2)

async def main():
    worker = AlloraWorker(
        topic_id=69,
        predict_fn=my_model,
        api_key="...",
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            print(f"error: {str(result)}")
            continue
        print(f"Prediction submitted to Allora: {result.prediction}")

asyncio.run(main())
