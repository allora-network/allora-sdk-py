#!/usr/bin/env python3
"""
Simple AlloraWorker test - exactly what an ML engineer would type in Jupyter.
"""

import asyncio
from allora_sdk.protobuf_client.tx_manager import FeeTier
from allora_sdk.worker import AlloraWorker

def my_model():
    """Your ML model goes here."""
    import random
    return round(55000 + random.uniform(-5000, 5000), 2)

async def run_worker(worker_id: int, topic_id: int):
    """Run a single worker and prefix output with worker ID."""
    worker = AlloraWorker(
        topic_id=topic_id,
        predict_fn=my_model,
        fee_tier=FeeTier.PRIORITY,
        mnemonic="mixture wrap brush symbol conduct patch sheriff urge merit antique sorry eagle",
        debug=True,
    )
    print(f"Worker {worker_id} (topic {topic_id}): {worker}")
    acct = await worker.client.get_account_info(str(worker.wallet.address()))
    print(f"Worker {worker_id} ACCOUNT: (type = {type(acct).__name__}) = {acct}")
    
    async for result in worker.run():
        print(f"Worker {worker_id} (topic {topic_id}): {result}")

async def main():
    # Create tasks for both workers
    task1 = asyncio.create_task(run_worker(1, 13))
    # task2 = asyncio.create_task(run_worker(2, 14))
    
    # Run both workers concurrently
    # await asyncio.gather(task1, task2)
    await asyncio.gather(task1)

if __name__ == "__main__":
    asyncio.run(main())