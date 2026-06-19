

# Allora Network Python SDK

A Python SDK for interacting with the Allora Network. Submit machine learning predictions, query blockchain data, and access network inference results.

## Table of Contents

- [Installation](#installation)
- [Allora Chain Overview](#allora-chain-overview)
- [ML Inference Worker](#ml-inference-worker)
  - [Quick Start](#quick-start)
  - [Advanced Configuration](#advanced-configuration)
- [Allora RPC Client](#rpc-client)
  - [Basic Usage](#basic-usage-1)
  - [Capabilities](#capabilities)
- [Allora API Client](#api-client)
  - [Basic Usage](#basic-usage-2)
  - [Features](#features)
- [Command-line Tools](#command-line-tools)
  - [allora-export-txs](#allora-export-txs)
  - [allora-topic-lifecycle-visualizer](#allora-topic-lifecycle-visualizer)
- [Development](#development)
  - [Prerequisites](#prerequisites)
  - [Setup for development](#setup-for-development)
  - [Testing](#testing)
  - [Code Generation](#code-generation)
  - [Workflow](#workflow)
  - [Dependencies](#dependencies)

## Installation

```bash
pip install allora_sdk
```

## Allora Chain Overview

The ALLO token is the native "compute gas" currency of the Allora Network, a decentralized oracle platform that leverages machine learning to provide accurate and timely data to smart contracts. The network operates on a proof-of-stake consensus mechanism, ensuring security and scalability.

ALLO has 18 decimal places, unlike the native token on most Cosmos chains.  This was chosen for compatibility with the Ethereum/EVM standard.

## ML Inference Worker

Submits predictions to Allora Network topics with your ML models. The worker handles wallet creation, blockchain transactions, and automatic retries so that you can focus on model engineering.

### Quick Start

The simplest way to start participating in the Allora network is to paste the following snippet into a Jupyter or Google Colab notebook (or just a Python file that you can run from your terminal).  It will automatically handle all of the network onboarding and configuration behind the scenes, and will start submitting inferences automatically.

A slightly more complete example can be found in the file `example_inference_worker.py`.

```python
from allora_sdk import AlloraNetworkConfig, AlloraWorker, RunContext

async def run_model(context: RunContext) -> float:
    # your ML model prediction logic
    return 123.45

async def main():
    worker = AlloraWorker.inferer(
        topic_id=69,
        network=AlloraNetworkConfig.testnet(),
        run=run_model,
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            print(f"Inference worker error: {result}")
        else:
            print(f"Prediction submitted to Allora: {result.submission}")

# IF YOU'RE RUNNING IN A PYTHON FILE:
import asyncio
asyncio.run(main())

# IF YOU'RE RUNNING IN A NOTEBOOK:
await main()
```

When you run this snippet, a few things happen:
- It configures this worker to communicate with our "testnet" network -- a place where no real funds are exchanged.
- It automatically generates an identity on the platform for you, represented by an `allo` address.  This identity is saved in the same folder from which you run the worker script, and will be auto-detected if you run it again later.
- It obtains a small amount of ALLO, the compute gas currency of the platform.
- It registers your worker to start submitting inferences to [Allora's "sandbox" topic](https://testnet.explorer.allora.network/topics/69) -- a topic for newcomers to figure out their configuration and setup, and to become accustomed to how things work on the platform. **There are no penalties for submitting inaccurate inferences to this topic.**

More resources:
- [Forge Builder Kit](https://github.com/allora-network/allora-forge-builder-kit): walks you through the entire process of training a simple model from Allora datasets and deploying it on the network
- Official [documentation](https://docs.allora.network)
- Join our [Discord server](https://discord.gg/RU7yPcqb)

### Advanced Configuration

```python
from allora_sdk import AlloraWorker, FeeTier, AlloraWalletConfig, AlloraNetworkConfig
from allora_sdk.worker.autostake import AutoStakeConfig, AutoStakeTargetType
from allora_sdk.worker import SanityCheckConfig

inference_worker = AlloraWorker.inferer(
    #
    # Wallet config
    #
    # Initialize with a mnemonic
    wallet=AlloraWalletConfig(mnemonic="..."),

    #
    # Networking config
    #

    # Helpers for common networks/environments
    # network = AlloraNetworkConfig.testnet(),
    # network = AlloraNetworkConfig.mainnet(),
    # network = AlloraNetworkConfig.local(),

    # Specify network options directly
    network=AlloraNetworkConfig(
        chain_id = "allora-testnet-1",
        url = "grpc+https://allora-grpc.testnet.allora.network:443",
        websocket_url = "wss://allora-rpc.testnet.allora.network/websocket",
        fee_denom = "uallo",
        fee_minimum_gas_price = 250_000_000.0,
        congestion_aware_fees = True,
        use_dynamic_gas_price = True,
    ),

    # Topic ID (see https://explorer.allora.network for the full list)
    topic_id=1,

    # Specify the inference function directly
    run=my_model,

    # Allora API key -- see https://developer.allora.network for a free key.
    # This is a convenience feature that allows the worker to fetch ALLO for gas fees on testnet.
    api_key="UP-...",

    # `fee_tier` controls how much you pay to ensure your inferences are included within
    # an epoch.  The options are ECO, STANDARD, or PRIORITY -- default is STANDARD.
    fee_tier=FeeTier.PRIORITY,

    # `debug` enables debug logging -- very noisy.
    debug=True,

    # Optional: auto-stake this worker's rewards to a reputer (Allora emissions module)
    autostake=AutoStakeConfig(
        target_type=AutoStakeTargetType.REPUTER,
        target_address="allo1...reputer",
    ),

    # Or: auto-stake to a Cosmos validator (staking MsgDelegate)
    # autostake = AutoStakeConfig(
    #     target_type=AutoStakeTargetType.VALIDATOR,
    #     target_address="allovaloper1...validator",
    # ),

    # Optional sanity check (default: enabled, 60s throttle)
    # sanity_check=SanityCheckConfig(enabled=False),  # disable
    # sanity_check=SanityCheckConfig(throttle_interval_seconds=30.0),
)
```

### Reputer Configuration

Reputers evaluate inference quality by computing losses between ground truth and predictions. An simple reputer can be built with the SDK just as easily as an inference worker. The main difference, is that instead of the `run_model` callback we need to pass a `reputer_fn` which has the type `(context: RunContext, inference: float) -> float`. This function gets called once for each inference in the epoch (that is, the network inference, each individual worker's inference, and a few additional "one-out" inferences which the network uses for scoring). It is expected to obtain a ground truth value, compare it to the inference, and return a loss which is computed with the topic-defined loss function. 

In practice, it is often more convenient to have two separate callbacks:
1. A ground truth function `get_ground_truth(context: RunContext) -> GroundTruthType` which only runs once per epoch.
2. A loss function `loss(gt: GroundTruthType, inference: float) -> float`, which runs for every value in the inference bundle

Note that the ground truth type does not need to agree with the inference type (`float`). That is useful for certain loss functions which require extra data. For example, the `czar` and `ztae` loss functions require a standard deviation of historical values, which can just be treated as part of the ground truth.

We provide a helper function `make_reputer_function` which takes two functions `get_ground_truth` and `loss_fn` as above and returns a `reputer_fn` that is suitable for passing to the Allora worker. It handles the caching of the ground truth, so that `get_ground_truth` only needs to be called once.

```python
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

# ...

worker = AlloraWorker.reputer(
    topic_id=69,
    network=AlloraNetworkConfig.testnet(),
    reputer_fn=make_reputer_function(get_ground_truth, mse_loss),
    min_stake_uallo=1_000_000,
)
```

A full example is in `example_reputer.py`.

## Allora RPC Client

Low-level blockchain client for advanced users. Supports queries, transactions, and WebSocket subscriptions.

### Basic Usage

Initialization is very flexible and straightforward.  The client can be initialized with:
- sensible preset defaults for testnet, mainnet, and local nodes
- direct specification of network and wallet parameters
- environment variables

```python
from allora_sdk import AlloraRPCClient, AlloraWalletConfig, AlloraNetworkConfig

# Initialize client manually
client = AlloraRPCClient(
    wallet=AlloraWalletConfig(
        mnemonic="...", # wallet config is optional, only needed for sending transactions
        prefix="allo",  # bech32 prefix (default is "allo" for Allora Network)
    ),
    network=AlloraNetworkConfig(
        url="...",           # RPC url
        websocket_url="...", # websocket url is optional, only needed for subscribing to events
    )
)

# Initialize client with preset network config defaults (testnet in this case)
client = AlloraRPCClient.testnet()

# Initialize client with preset network config, but some defaults overridden
client = AlloraRPCClient.testnet(
    wallet=AlloraWalletConfig(mnemonic-"..."), # optional, only needed for sending transactions
    websocket_url="...",                       # optional, only needed for subscribing to events
)

# Alternatively, initialize client from environment variables:
#   - PRIVATE_KEY
#   - MNEMONIC
#   - MNEMONIC_FILE
#   - ADDRESS_PREFIX
#   - CHAIN_ID
#   - RPC_ENDPOINT
#   - WEBSOCKET_ENDPOINT
#   - FAUCET_URL
#   - FEE_DENOM
#   - FEE_MIN_GAS_PRICE
client = AlloraRPCClient.from_env()

# Query network data
# Note: `height` is optional.  Defaults to the latest block on the chain.
request = GetLatestRegretStdNormRequest(topic_id=123)
response = client.emissions.query.get_latest_regret_std_norm(request, height=6200000) 

# Submit transactions  
response = await client.emissions.tx.insert_worker_payload(
    topic_id=1,
    inference_value="55000.0",
    nonce=12345
)

# WebSocket event subscriptions
from allora_sdk.rpc_client.protos.emissions.v9 import EventWorkerSubmissionWindowOpened

async def handle_event(event, block_height):
    print(f"New epoch: {event.topic_id} at block {block_height}")

subscription_id = await client.events.subscribe_new_block_events_typed(
    emissions.EventWorkerSubmissionWindowOpened,
    [ EventAttributeCondition("topic_id", "=", "1") ],
    handle_event
)
```

### Capabilities

RPC wire protocols:
- **gRPC API**
- **Cosmos-LCD REST API**

Modules:
- github.com/allora-network/x/emissions
- github.com/allora-network/x/mint
- auth
- bank
- feemarket
- mint
- tendermint
- tx

Wire protocol is determined by the RPC url string passed to the config constructor:
- `grpc+http(s)` will use the gRPC Protobuf client
- `rest+http(s)` will use the Cosmos-LCD client

- **Transaction support**: Fee estimation, signing, and broadcasting  
- **WebSocket events**: Real-time blockchain event subscriptions.  For a usage example, see the `AlloraWorker`
- **Multi-chain**: Testnet and mainnet support come with batteries included, but there is maximal configurability.  Can be used with other Cosmos SDK chains.
- **Type safety**: Full protobuf type and service definitions, codegen clients

### Privy-Managed (Delegated) Signing

By default the worker signs with a local key (mnemonic / private key / `.allora_key`).
Alternatively, you can delegate signing to the Forge backend, which signs with a
Privy-managed server wallet — the worker holds no private key. `make_remote_wallet()`
returns a cosmpy `Wallet` you pass via `AlloraWalletConfig(wallet=...)`; the worker loop
and both tx + bundle signing work unchanged.

```python
import os
from allora_sdk import (
    AlloraWorker, AlloraNetworkConfig, AlloraWalletConfig, make_remote_wallet,
)

# wallet_id + api_key are minted in the Forge web app.
remote = make_remote_wallet(
    backend_url="https://forge.allora.network",
    api_key=os.environ["FORGE_API_KEY"],
    wallet_id=os.environ["FORGE_SIGNING_WALLET_ID"],
)

worker = AlloraWorker.inferer(
    topic_id=69,
    network=AlloraNetworkConfig.testnet(),
    wallet=AlloraWalletConfig(
        wallet=remote,
        # Subsidize gas from a master wallet via feegrant (optional).
        fee_granter=os.environ.get("FEE_GRANTER"),
    ),
    run=run_model,
)
```

Worker gas can be subsidized by a master-wallet feegrant: set `fee_granter` to the master
wallet address (which must hold an on-chain feegrant allowance for the signing wallet) and
transactions are broadcast with that granter as the fee payer, so the signing wallet needs
no ALLO of its own. Leave `fee_granter` unset to have the signing wallet pay its own fees.

For env-driven (12-factor) deployments, `AlloraWalletConfig.from_env()` builds the remote
wallet automatically when `FORGE_API_KEY` and `FORGE_SIGNING_WALLET_ID` are set
(`FORGE_BACKEND_URL` defaults to `https://forge.allora.network`), alongside the existing
`PRIVATE_KEY` / `MNEMONIC` / `MNEMONIC_FILE` options. Set `FEE_GRANTER` to enable the
feegrant subsidy described below.

## Allora API Client

Slim, high-level HTTP client for querying a list of all topics, individual topic metadata, and network inference results.

**NOTE:** you will need an Allora API key.  You can obtain one for free at [https://developer.allora.network](https://developer.allora.network).

### Basic Usage

```python
import asyncio
from allora_sdk.api_client import AlloraAPIClient

client = AlloraAPIClient()

async def main():
    # Get all active topics
    topics = await client.get_all_topics()
    print(f"Found {len(topics)} topics")

    # Get latest inference
    inference = await client.get_inference_by_topic_id(13)
    print(f"ETH price in 5 minutes: ${inference.inference_data.network_inference_normalized}")

asyncio.run(main())
```

### Features

- **Price predictions**: BTC, ETH, SOL, etc. across multiple timeframes
- **Topic index**: Browse all network topics and their metadata  
- **Confidence intervals**: Access prediction uncertainty bounds
- **Async/await**: Fully asynchronous API


## Command-line Tools

The SDK comes with several command-line tools that provide useful insights into the Allora Network.  Running `pip install allora_sdk` will make them available in your environment.

### `allora-export-txs`

This tool allows the user to export all of the inference worker transactions from the given account to a CSV file.

```
usage: allora-export-txs [-h] --address ADDRESS [--url URL] [--page_size PAGE_SIZE] [--pages PAGES]
                         [--start_page START_PAGE] [--resume | --no-resume] [--order ORDER]
                         [--output_file OUTPUT_FILE]

Export Allora inference worker transactions from an address to CSV

options:
  -h, --help            show this help message and exit
  --address ADDRESS     The address to fetch transactions for
  --url URL             The URL of the RPC endpoint
  --page_size PAGE_SIZE
                        The number of txs to fetch per request (lower if you have issues)
  --pages PAGES         The total number of pages to fetch
  --start_page START_PAGE
                        The page on which to start fetching (useful with --resume)
  --resume, --no-resume
                        Set to true if you want to resume an existing fetch
  --order ORDER         'desc' to start from most recent or 'asc' to start from oldest
  --output_file OUTPUT_FILE
                        Output CSV file path (default: transactions.csv)
````


### `allora-topic-lifecycle-visualizer`

Given a set of logs from the `AlloraWorker`, this tool plots a visualization of the phases of a topic's lifecycle over the provided block range.

```
usage: Plot a visualization of a topic's lifecycle over the given block range [-h] --log_file LOG_FILE

options:
  -h, --help           show this help message and exit
  --log_file LOG_FILE  AlloraWorker log file
````


## Development

This project uses modern Python tooling for development and supports Python 3.10-3.13.

### Prerequisites

Install [uv](https://docs.astral.sh/uv/).  Instructions available at [https://docs.astral.sh/uv/getting-started/installation](https://docs.astral.sh/uv/getting-started/installation).

```bash
# Example with curl:
curl -LsSf https://astral.sh/uv/install.sh | sh

# Example with pip:
pip install uv
```

### Setup for development

The Makefile handles almost all of the development setup.  Simply run:

```bash
uv venv
source .venv/bin/activate
make dev
```

### Testing

The project uses tox for testing across Python versions:

```bash
# Run all tests across supported Python versions using `tox`
make test

# Test specific Python version
tox -e py312
```

### Code Generation

The SDK uses two code generation systems:

**gRPC Generation:**
- Generates async Python clients from .proto files
- Sources: Cosmos SDK, Allora Chain, googleapis
- Output: `src/allora_sdk/rpc_client/grpc/`
- Command: `make grpc`

**REST Client Generation:**
- Analyzes protobuf HTTP annotations to generate REST clients  
- Matches gRPC client interfaces exactly
- Sources: Same .proto files as above
- Output: `src/allora_sdk/rpc_client/rest/`
- Command: `make rest`

Both generators run automatically with `make dev`.

### Full Workflow

```bash
# Initial setup
uv venv
source .venv/bin/activate
make dev

# After changes to .proto files
rm -rf src/allora_sdk/rpc_client/rest
rm -rf src/allora_sdk/rpc_client/grpc
rm -rf src/allora_sdk/rpc_client/interfaces
rm -rf src/allora_sdk/rpc_client/protos
make dev

# Run tests  
tox

# Build wheel for distribution
make wheel      # or: uv build
```

### Dependencies

- **Runtime dependencies**: Defined in `pyproject.toml` under `dependencies`
- **Development dependencies**: Under `[project.optional-dependencies.dev]`  
- **Code generation**: Under `[project.optional-dependencies.codegen]`



