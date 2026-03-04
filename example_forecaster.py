"""Example: forecaster worker using an HTTP API for forecast data.

Shows two approaches:
  1. Direct Python function (simple, good for notebooks)
  2. API-backed function via make_api_forecaster_fn (good for sidecar containers)

Expected API response format:
    {"forecasts": {"allo1inferer1...": 3500.0, "allo1inferer2...": 3510.5}}
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

logger = logging.getLogger(__name__)


# ── Option 1: direct Python function ────────────────────────────────────


def run_model(nonce: int) -> dict[str, float]:
    return {
        "allo1inferer1...": 10.0,
    }


# ── Option 2: API-backed function ───────────────────────────────────────

api_run_model = make_api_forecaster_fn(
    APISourceConfig(url="http://localhost:8000/forecast?block={nonce}")
)


async def main():
    worker = AlloraWorker.forecaster(
        topic_id=69,
        wallet=AlloraWalletConfig(mnemonic="..."),
        network=AlloraNetworkConfig.testnet(),
        run=api_run_model,  # swap to `run_model` for direct function
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logger.error("Forecaster worker error: %s", result)
            continue
        print(f"Forecast submitted to Allora: {result.submission}")


asyncio.run(main())
