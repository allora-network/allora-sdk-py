#!/usr/bin/env python3
"""
Simple AlloraWorker test - exactly what an ML engineer would type in Jupyter.
"""

import asyncio

from cosmpy.mnemonic import generate_mnemonic
from allora_sdk.rpc_client.config import AlloraWalletConfig
from allora_sdk.worker import AlloraWorker

def my_model(nonce: int):
    """Your ML model goes here."""
    import random
    return round(116000 + random.uniform(-5000, 5000), 2)

async def run_worker(topic_id: int, mnemonic: str, api_key: str):
    worker = AlloraWorker(
        topic_id=69,
        predict_fn=my_model,
        api_key=api_key,
        wallet=AlloraWalletConfig(mnemonic=mnemonic),
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            print(f"error: {str(result)}")
            continue
        print(f"Prediction submitted to Allora: {result.prediction}")

async def main():
    w1 = asyncio.create_task(run_worker(69, generate_mnemonic(), "11"))
    w2 = asyncio.create_task(run_worker(69, generate_mnemonic(), "22"))
    w3 = asyncio.create_task(run_worker(69, generate_mnemonic(), "33"))
    w4 = asyncio.create_task(run_worker(69, generate_mnemonic(), "44"))
    await asyncio.gather(w1, w2, w3, w4)

asyncio.run(main())
