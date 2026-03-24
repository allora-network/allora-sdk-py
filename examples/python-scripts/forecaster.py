"""Minimal forecaster — run directly with: python forecaster.py

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
    make_api_forecaster_fn,
)

logging.basicConfig(level=logging.INFO)


def run_model(nonce: int) -> dict[str, float]:
    return {
        "allo1inferer1...": 3500.0,
        "allo1inferer2...": 3510.5,
    }


api_run = make_api_forecaster_fn(
    APISourceConfig(url="http://localhost:8000/forecast?block={nonce}")
)


async def main():
    worker = AlloraWorker.forecaster(
        topic_id=69,
        wallet=AlloraWalletConfig(mnemonic="your twelve word mnemonic ..."),
        network=AlloraNetworkConfig.testnet(),
        run=run_model,  # swap to api_run for HTTP API
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logging.error("Error: %s", result)
            continue
        print(f"Submitted forecast: {result.submission}")


asyncio.run(main())
