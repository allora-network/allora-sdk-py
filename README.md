

# Allora Network Python SDK

A Python SDK for interacting with the Allora Network. Submit machine learning predictions, query blockchain data, and access network inference results.

## Table of Contents

- [Installation](#installation)
- [Allora Chain Overview](#allora-chain-overview)
- [ML Inference Worker](#ml-inference-worker)
  - [Quick Start](#quick-start)
  - [Advanced Configuration](#advanced-configuration)
- [RPC Client](#rpc-client)
  - [Basic Usage](#basic-usage-1)
  - [Capabilities](#capabilities)
- [API Client](#api-client)
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
from allora_sdk.worker import AlloraWorker
import asyncio

def my_model():
    # Your ML model prediction logic
    return 120000.0  # Example BTC price prediction

async def main():
    worker = AlloraWorker(
        predict_fn=my_model,
        api_key="<YOUR API KEY HERE>",
    )
    
    async for result in worker.run():
        if isinstance(result, Exception):
            print(f"Error: {result}")
        else:
            print(f"Prediction submitted: {result.prediction}")

# IF YOU'RE RUNNING IN A PYTHON FILE:
asyncio.run(main())

# IF YOU'RE RUNNING IN A NOTEBOOK:
await main()
```

When you run this snippet, a few things happen:
- It configures this worker to communicate with our "testnet" network -- a place where no real funds are exchanged.
- It automatically generates an identity on the platform for you, represented by an `allo` address.
- It obtains a small amount of ALLO, the compute gas currency of the platform.
- It registers your worker to start submitting inferences to [Allora's "sandbox" topic](https://explorer.allora.network/topics/69) -- a topic for newcomers to figure out their configuration and setup, and to become accustomed to how things work on the platform. **There are no penalties for submitting poor inferences to this topic.**

More resources:
- [Forge Builder Kit](https://github.com/allora-network/allora-forge-builder-kit/blob/main/notebooks/Allora%20Forge%20Builder%20Kit.ipynb): walks you through the entire process of training a simple model from Allora datasets and deploying it on the network
- Official [documentation](https://docs.allora.network)
- Join our [Discord server](https://discord.gg/RU7yPcqb)

### Advanced Configuration

```python
from allora_sdk.worker import AlloraWorker
from allora_sdk.rpc_client.tx_manager import FeeTier

worker = AlloraWorker(
    topic_id=1,

    # Specify the inference function directly
    predict_fn=my_model,
    # Or specify a pickle file containing it (recommended to use the `dill` package for this)
    predict_pkl="my_model.pkl",

    #
    # These parameters give you the freedom to manage your identity on the platform as you prefer
    #
    mnemonic_file="./my_key",      # Custom mnemonic file location. Default is `./allora_key`.
    mnemonic="foo bar baz ...",    # Mnemonic phrase if you prefer to specify it directly.
    private_key="b381fa9cc20d...", # Hex-encoded 32-byte private key string.
    api_key="UP-...",              # Allora API key -- see https://developer.allora.network for a free key.

    # `fee_tier` controls how much you pay to ensure your inferences are included within an epoch.  The options are ECO, STANDARD, or PRIORITY -- default is STANDARD.
    fee_tier=FeeTier.PRIORITY,     # 

    # `debug` enables debug logging -- noisy, but good for debugging.
    debug=True,
)
```

## RPC Client

Low-level blockchain client for advanced users. Supports queries, transactions, and WebSocket subscriptions.

### Basic Usage

Initialization is very flexible and straightforward.  The client can be initialized with:
- sensible preset defaults for testnet, mainnet, and local nodes
- direct specification of network and wallet parameters
- environment variables

```python
from allora_sdk import LocalWallet, PrivateKey
from allora_sdk.rpc_client import AlloraRPCClient
from allora_sdk.protos.emissions.v9 import GetActiveTopicsAtBlockRequest, EventNetworkLossSet

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

# Initialize client with preset network config defaults
client = AlloraRPCClient.testnet()
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
topics = client.emissions.query.get_active_topics_at_block(
    emissions.GetActiveTopicsAtBlockRequest(block_height=1000)
)

# Submit transactions  
response = await client.emissions.tx.insert_worker_payload(
    topic_id=1,
    inference_value="55000.0",
    nonce=12345
)

# WebSocket subscriptions
async def handle_event(event, block_height):
    print(f"New epoch: {event.topic_id} at block {block_height}")

subscription_id = await client.events.subscribe_new_block_events_typed(
    emissions.EventNetworkLossSet,
    [ EventAttributeCondition("topic_id", "=", "1") ],
    handle_event
)
```

### Capabilities

RPC endpoint types:
- **gRPC API**: All emissions, bank, and staking operations
- **Cosmos-LCD REST API**: Same as above with identical interfaces

Determined by the RPC url string passed to the config constructor.  `grpc+http(s)` will utilize the gRPC Protobuf client, whereas `rest+http(s)` will use Cosmos-LCD.

- **Transaction support**: Fee estimation, signing, and broadcasting  
- **WebSocket events**: Real-time blockchain event subscriptions.  For a usage example, see the `AlloraWorker`
- **Multi-chain**: Testnet and mainnet support come with batteries included, but there is maximal configurability.  Can be used with other Cosmos SDK chains.
- **Type safety**: Full protobuf type and service definitions, codegen clients

## API Client

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
- **Topic discovery**: Browse all network topics and their metadata  
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

Install [uv](https://docs.astral.sh/uv/) (recommended) or use pip:

```bash
# Install uv (recommended)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or use pip
pip install uv
```

### Setup for development

The Makefile handles all development setup.  Simply run:

```bash
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

**Protobuf Generation (betterproto2):**
- Generates async Python clients from .proto files
- Sources: Cosmos SDK, Allora Chain, googleapis
- Output: `src/allora_sdk/protos/`
- Command: `make proto`

**REST Client Generation (custom):**
- Analyzes protobuf HTTP annotations to generate REST clients  
- Matches gRPC client interfaces exactly
- Sources: Same .proto files as above
- Output: `src/allora_sdk/rest/`
- Command: `make generate_rest_clients`

Both generators run automatically with `make dev`.

### Workflow

```bash
# Initial setup
make dev

# After changes to .proto files
make proto generate_rest_clients

# Run tests  
tox

# Build wheel for distribution
make wheel      # or: uv build
```

### Dependencies

- **Runtime dependencies**: Defined in `pyproject.toml` under `dependencies`
- **Development dependencies**: Under `[project.optional-dependencies.dev]`  
- **Code generation**: Under `[project.optional-dependencies.codegen]`

The project pins specific versions of crypto dependencies (cosmpy, betterproto2) while allowing flexibility for general-purpose libraries.


