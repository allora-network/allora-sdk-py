

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

**NOTE:** you will need an Allora API key.  You can obtain one for free at [https://developer.allora.network](https://developer.allora.network).

```python
from allora_sdk import AlloraWorker

def my_model():
    # Your ML model prediction logic
    return 120000.0  # Example BTC price prediction

async def main():
    worker = AlloraWorker.testnet(
        run=my_model,
        api_key="<YOUR API KEY HERE>",
    )
    
    async for result in worker.run():
        if isinstance(result, Exception):
            print(f"Error: {result}")
        else:
            print(f"Prediction submitted: {result.prediction}")

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
)
```

### Reputer Configuration

Reputers evaluate inference quality by computing losses between ground truth and predictions. The SDK supports automatic loss function selection based on the topic's on-chain configuration.

```python
from allora_sdk.worker import AlloraWorker

async def get_ground_truth(nonce: int) -> float:
    # Return the actual value for the given epoch/nonce
    return 100.0

# Simplest reputer setup - loss function auto-selected based on topic config
reputer = AlloraWorker.reputer(
    ground_truth_fn=get_ground_truth,
    topic_id=1,
    api_key="UP-...",
)

# Custom loss function example
def my_custom_loss(ground_truth: float, predicted: float) -> float:
    return abs(ground_truth - predicted)  # MAE

reputer_custom = AlloraWorker.reputer(
    ground_truth_fn=get_ground_truth,
    loss_fn=my_custom_loss,  # Override auto-selection
    topic_id=1,
    api_key="UP-...",
)
```

**Supported Default Loss Methods:**

When `loss_fn` is not provided, the SDK auto-selects from these implementations based on the topic's `loss_method`:

| Method | Aliases | Description |
|--------|---------|-------------|
| `sqe` | `mse`, `squared_error` | Squared Error |
| `abse` | `mae`, `absolute_error` | Absolute Error |
| `huber` | - | Huber Loss (delta=1.0) |
| `logcosh` | `log_cosh` | Log-Cosh Loss |
| `bce` | `binary_cross_entropy` | Binary Cross Entropy |
| `poisson` | - | Poisson Loss |
| `ztae` | - | Z-Transformed Absolute Error* |
| `zptae` | - | Z Power-Tanh Absolute Error* |

*Note: `ztae` and `zptae` require a standard deviation (std) parameter. The default functions use std=1.0. For proper use, create custom functions with `make_ztae_loss(std=...)` or `make_zptae_loss(std=...)`.

You can also use these loss functions directly:

```python
from allora_sdk.loss_methods import (
    get_default_loss_fn,
    squared_error_loss,
    absolute_error_loss,
    huber_loss,
    make_ztae_loss,
    make_zptae_loss,
)

# Get by name
loss_fn = get_default_loss_fn("mse")

# Or use directly
loss = squared_error_loss(ground_truth=100.0, predicted=95.0)  # Returns 25.0

# For ztae/zptae, create with your data's std:
ztae = make_ztae_loss(std=0.02)  # std = historical volatility
loss = ztae(ground_truth=0.01, predicted=0.02)

zptae = make_zptae_loss(std=0.02, alpha=0.25, beta=2.0)
loss = zptae(ground_truth=0.01, predicted=0.02)
```

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



