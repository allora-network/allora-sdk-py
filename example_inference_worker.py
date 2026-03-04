"""Example: inferer worker using an HTTP API for predictions.

Shows two approaches:
  1. Direct Python function (simple, good for notebooks)
  2. API-backed function via make_api_inferer_fn (good for sidecar containers)

Switch between them by changing the `run=` argument.
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

logger = logging.getLogger(__name__)


# ── Option 1: direct Python function ────────────────────────────────────


def run_model(nonce: int):
    return 10


# ── Option 2: API-backed function ───────────────────────────────────────

api_run_model = make_api_inferer_fn(
    APISourceConfig(url="http://localhost:8000/inference?block={nonce}")
)


async def main():
    worker = AlloraWorker.inferer(
        topic_id=69,
        wallet=AlloraWalletConfig(mnemonic="..."),
        network=AlloraNetworkConfig.testnet(),
        run=api_run_model,  # swap to `run_model` for direct function
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logger.error("Inference worker error", exc_info=result)
            continue
        print(f"Prediction submitted to Allora: {result.submission}")


asyncio.run(main())
