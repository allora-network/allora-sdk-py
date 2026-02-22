import asyncio
from allora_sdk import AlloraNetworkConfig, AlloraWalletConfig, AlloraWorker

def get_ground_truth(nonce: int) -> float:
    return 10.5

async def main():
    worker = AlloraWorker.reputer(
        topic_id=69,
        wallet=AlloraWalletConfig(mnemonic=""),
        network=AlloraNetworkConfig.testnet(),
        ground_truth_fn=get_ground_truth,
        min_stake_uallo=10000000000000000000,
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            continue
        print(f"Reputer payload submitted to Allora: {result.submission}")

asyncio.run(main())
