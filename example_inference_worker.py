import asyncio
import logging
from allora_sdk import AlloraNetworkConfig, AlloraWalletConfig, AlloraWorker

logger = logging.getLogger(__name__)


def run_model(nonce: int):
    return 10

async def main():
    worker = AlloraWorker.inferer(
        topic_id=69,
        wallet=AlloraWalletConfig(mnemonic="..."),
        network=AlloraNetworkConfig.testnet(),
        run=run_model,
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logger.error("Inference worker error: %s", result)
            continue
        print(f"Prediction submitted to Allora: {result.submission}")

asyncio.run(main())





