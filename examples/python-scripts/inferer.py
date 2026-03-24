"""Minimal inferer — run directly with: python inferer.py

Shows two data source options:
  1. run_model  — your own Python function (default)
  2. api_run    — calls an HTTP API endpoint

Switch between them by changing the run= argument.
"""

import asyncio
import logging

from allora_sdk import (
    APISourceConfig,
    AlloraNetworkConfig,
    AlloraWalletConfig,
    AlloraWorker,
    make_api_inferer_fn,
)

logging.basicConfig(level=logging.INFO)


def run_model(nonce: int) -> float:
    return 3521.50


api_run = make_api_inferer_fn(
    APISourceConfig(url="http://localhost:8000/inference?block={nonce}")
)


async def main():
    worker = AlloraWorker.inferer(
        topic_id=69,
        wallet=AlloraWalletConfig(mnemonic="your twelve word mnemonic ..."),
        network=AlloraNetworkConfig.testnet(),
        run=run_model,  # swap to api_run for HTTP API
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logging.error("Error: %s", result)
            continue
        print(f"Submitted prediction: {result.submission}")


asyncio.run(main())
