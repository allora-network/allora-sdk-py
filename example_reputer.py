"""Example: reputer worker using an HTTP API for ground truth data.

Shows two approaches:
  1. Direct Python function (simple, good for notebooks)
  2. API-backed function via make_api_ground_truth_fn (good for sidecar containers)
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

logger = logging.getLogger(__name__)


# ── Option 1: direct Python function ────────────────────────────────────


def get_ground_truth(nonce: int) -> float:
    return 10.5


# ── Option 2: API-backed function ───────────────────────────────────────

api_ground_truth = make_api_ground_truth_fn(
    APISourceConfig(url="http://localhost:8001/ground-truth?block={nonce}")
)


async def main():
    worker = AlloraWorker.reputer(
        topic_id=69,
        wallet=AlloraWalletConfig(mnemonic=""),
        network=AlloraNetworkConfig.testnet(),
        ground_truth_fn=api_ground_truth,  # swap to `get_ground_truth` for direct
        min_stake_uallo=10000000000000000000,
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logger.error("Reputer worker error: %s", result)
            continue
        print(f"Reputer payload submitted to Allora: {result.submission}")


asyncio.run(main())
