import asyncio
import aiostream
import logging
import yaml
from allora_sdk import AlloraNetworkConfig, AlloraWalletConfig, AlloraWorker, RunContext, get_block_time, make_reputer_function
from datetime import datetime, timedelta
from ground_truth import make_gt_function
from losses import LOSS_FUNCTIONS
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

async def main():
    topic_definitions = yaml.safe_load(open(Path(__file__).parent / 'topics.yaml'))

    submit_lock = asyncio.Lock()

    workers = []
    for (topic_id, config) in topic_definitions.items():
        mode = config['ground_truth_method']
        ticker = config['ticker']
        loss_method = config['loss_method']
        lookback = timedelta(seconds = config['timeframe'])

        reputer_fn = make_reputer_function(
            make_gt_function(mode, ticker, lookback), 
            LOSS_FUNCTIONS[loss_method]
        )

        worker = AlloraWorker.reputer(
            topic_id = topic_id,
            network = AlloraNetworkConfig.testnet(),
            reputer_fn = reputer_fn,
            min_stake_uallo = 1_000_000,
            lock = submit_lock,
            show_banner = False,
            debug = False,
        )
        workers.append(worker)

    merged_stream = aiostream.stream.merge(*[worker.run() for worker in workers])

    async with merged_stream.stream() as streamer:
        async for result in streamer:
            if isinstance(result, Exception):
                logger.error("Reputer worker error: %s", result)

asyncio.run(main())
