import asyncio
from allora_sdk import AlloraNetworkConfig, AlloraWalletConfig, AlloraWorker

def run_model(nonce: int) -> dict[str, float]:
    return {
        "allo1inferer1...": 10.0,
    }

async def main():
    worker = AlloraWorker.forecaster(
        topic_id=69,
        wallet=AlloraWalletConfig(mnemonic="..."),
        network=AlloraNetworkConfig.testnet(),
        run=run_model,
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            continue
        print(f"Forecast submitted to Allora: {result.submission}")

asyncio.run(main())
