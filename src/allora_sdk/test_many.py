#!/usr/bin/env python3
"""
Simple AlloraWorker test - exactly what an ML engineer would type in Jupyter.
"""

import asyncio
import random
import string
import pprint

from cosmpy.aerial.wallet import LocalWallet
from cosmpy.mnemonic import generate_mnemonic
from allora_sdk.protos.cosmos.distribution.v1beta1 import ValidatorCurrentRewards, ValidatorCurrentRewardsRecord
from allora_sdk.protos.cosmos.staking.v1beta1 import Delegation
from allora_sdk.protos.emissions.v9 import EventReputerSubmissionWindowOpened
from allora_sdk.rpc_client.config import AlloraWalletConfig
from allora_sdk.worker import AlloraWorker

async def my_model(nonce: int):
    """Your ML model goes here."""
    import random
    return round(116000 + random.uniform(-5000, 5000), 2)

async def run_worker(topic_id: int, mnemonic_file: str, api_key: str):
    worker = AlloraWorker.inferer(
        topic_id=69,
        predict_fn=my_model,
        # api_key=api_key,
        wallet=AlloraWalletConfig(mnemonic_file=mnemonic_file),
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            print(f"error: {str(result)}")
            continue
        print(f"Prediction submitted to Allora: {result.prediction}")

def generate_mnemonics(count: int):
    for i in range(count):
        with open(f"mnemonic_{i}.txt", "w") as f:
            f.write(generate_mnemonic() + "\n")

async def main():
    # num_workers = 4

    # random_str = lambda: ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    # for i in range(num_workers):
    #     with open(f"mnemonic_{i}.txt", "r") as f:
    #         m = f.read()
    #         print(str(LocalWallet.from_mnemonic(m, prefix="allo").address()))

    # tasks = [ asyncio.create_task(run_worker(69, f"mnemonic_{i}.txt", random_str())) for i in [1] ]
    # await asyncio.gather(*tasks)
    worker = AlloraWorker.inferer(
        topic_id=69,
        predict_fn=my_model,
        # api_key=api_key,
        # wallet=AlloraWalletConfig(mnemonic_file=mnemonic_file),
    )

    await worker.client.events.subscribe_new_block_events_typed(
        EventReputerSubmissionWindowOpened, [], lambda evt, height: pprint.pprint(evt),
    )
    await asyncio.sleep(1000000)




asyncio.run(main())
