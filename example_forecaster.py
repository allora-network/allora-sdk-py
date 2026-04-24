import asyncio
import logging
from allora_sdk import AlloraNetworkConfig, AlloraWalletConfig, AlloraWorker, RunContext, ValueBundle, get_network_inference

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

async def run_model(ctx: RunContext) -> dict[str, float]:
    # get inferers who participated in the previous epoch
    epoch_length = 54
    value_bundle = await get_network_inference(ctx.client, ctx.topic_id, ctx.nonce - epoch_length)
    inferer_addrs = [x.worker for x in value_bundle.inferer_values]

    # predict loss for each inferer
    forecast = {addr: 1.23 for addr in inferer_addrs}

    return forecast

async def main():
    worker = AlloraWorker.forecaster(
        topic_id=69,
        # wallet=AlloraWalletConfig(mnemonic="..."),  # uncomment to use a specific wallet instead of generating one
        network=AlloraNetworkConfig.testnet(),
        run=run_model,
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logger.error("Forecaster worker error: %s", result)
            continue
        print(f"Forecast submitted to Allora: {result.submission}")

asyncio.run(main())
