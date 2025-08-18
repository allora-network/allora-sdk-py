#!/usr/bin/env python3
"""
Simple AlloraWorker test - exactly what an ML engineer would type in Jupyter.
"""

import asyncio
import logging
from allora_sdk.protobuf_client.tx_manager import FeeTier
from allora_sdk.worker import AlloraWorker

def my_model():
    """Your ML model goes here."""
    import random
    return round(55000 + random.uniform(-5000, 5000), 2)

async def run_worker(worker_id: int, topic_id: int):
    worker = AlloraWorker(
        topic_id=topic_id,
        predict_fn=my_model,
        fee_tier=FeeTier.PRIORITY,
        mnemonic="mixture wrap brush symbol conduct patch sheriff urge merit antique sorry eagle",
        log_level=logging.INFO,
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            # print(f"error: {str(result)}")
            continue
        print(f"Worker {worker_id} (topic {topic_id}): {result.prediction}")

async def main():
    # task1 = asyncio.create_task(run_worker(1, 11))
    task1 = asyncio.create_task(run_worker(1, 13))
    task2 = asyncio.create_task(run_worker(2, 14))
    task3 = asyncio.create_task(run_worker(3, 60))
    task4 = asyncio.create_task(run_worker(4, 61))
    task5 = asyncio.create_task(run_worker(5, 62))
    task6 = asyncio.create_task(run_worker(6, 63))
    
    await asyncio.gather(task1) #, task2, task3, task4, task5, task6)

if __name__ == "__main__":
    asyncio.run(main())