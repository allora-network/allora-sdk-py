"""Minimal reputer — run directly with: python reputer.py

Shows two data source options:
  1. get_ground_truth  — your own Python function (default)
  2. api_ground_truth  — calls an HTTP API endpoint

Switch between them by changing the ground_truth_fn= argument.
"""

import asyncio
import logging

from allora_sdk import (
    APISourceConfig,
    AlloraNetworkConfig,
    AlloraWalletConfig,
    AlloraWorker,
    make_api_ground_truth_fn,
)

logging.basicConfig(level=logging.INFO)


def get_ground_truth(nonce: int) -> float:
    return 3519.88


api_ground_truth = make_api_ground_truth_fn(
    APISourceConfig(url="http://localhost:8001/ground-truth?block={nonce}")
)


async def main():
    worker = AlloraWorker.reputer(
        topic_id=69,
        wallet=AlloraWalletConfig(mnemonic="your twelve word mnemonic ..."),
        network=AlloraNetworkConfig.testnet(),
        ground_truth_fn=get_ground_truth,  # swap to api_ground_truth for HTTP API
        min_stake_uallo=100_000_000,
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logging.error("Error: %s", result)
            continue
        print(f"Submitted reputer payload: {result.submission}")


asyncio.run(main())
