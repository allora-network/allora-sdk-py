import asyncio
import logging
import math
from allora_sdk import AlloraNetworkConfig, AlloraWalletConfig, AlloraWorker, RunContext, make_reputer_function, get_block_time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def mse_loss(x: float, y: float) -> float:
    return (x-y)**2

async def get_ground_truth(context: RunContext) -> float:
    nonce_time = await get_block_time(context.client, context.nonce)
    prediction_time = nonce_time.replace(second=0, microsecond=0) + timedelta(days = 1)

    logger.info(f'Generating ground truth for epoch starting at {nonce_time}')
    logger.info(f'As this is a 1 day BTC/USD price prediction topic, we need to get the price of BTC at {prediction_time}')

    # getting this from some data source
    ground_truth = 123.45

    return ground_truth

async def main():
    worker = AlloraWorker.reputer(
        topic_id=69,
        # wallet=AlloraWalletConfig(mnemonic="..."),  # uncomment to use a specific wallet instead of generating one
        network=AlloraNetworkConfig.testnet(),
        reputer_fn=make_reputer_function(get_ground_truth, mse_loss),
        min_stake_uallo=1_000_000,
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logger.error("Reputer worker error: %s", result)
            continue

asyncio.run(main())
