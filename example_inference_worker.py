import asyncio
import logging
from allora_sdk import AlloraNetworkConfig, AlloraWalletConfig, AlloraWorker, RunContext, get_block_time
from allora_sdk.rpc_client.tx_manager import TxExpiredError
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

async def run_model(context: RunContext) -> float:
    nonce_time = await get_block_time(context.client, context.nonce)
    prediction_time = nonce_time.replace(second=0, microsecond=0) + timedelta(days = 1)

    logger.info(f'Generate prediction for epoch starting at {nonce_time}')
    logger.info(f'As this is a 1 day BTC/USD price prediction topic, we need to the predict the price of BTC at {prediction_time}')

    # sophisticated machine learning model
    prediction = 123.45

    return prediction

async def main():
    worker = AlloraWorker.inferer(
        topic_id=69,
        # wallet=AlloraWalletConfig(mnemonic="..."),  # uncomment to use a specific wallet instead of generating one
        network=AlloraNetworkConfig.testnet(),
        run=run_model,
    )

    async for result in worker.run():
        if isinstance(result, TxExpiredError):
            # Expected terminal outcome: the submission window closed before the
            # payload landed. The worker already recorded the nonce and moved on.
            logger.warning(f"Submission window closed before the payload landed: {result}")
            continue
        if isinstance(result, Exception):
            logger.error(f"Inference worker error: {result}")
            continue
        logger.info(f"Prediction submitted to Allora: {result.submission}")

asyncio.run(main())
