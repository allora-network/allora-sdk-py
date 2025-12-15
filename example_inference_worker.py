import asyncio
from allora_sdk import AlloraNetworkConfig, AlloraWalletConfig, AlloraWorker

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
            continue
        print(f"Prediction submitted to Allora: {result.prediction}")

asyncio.run(main())





