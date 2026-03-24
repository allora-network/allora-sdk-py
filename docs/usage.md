# Usage Guide

This guide helps you get your model, forecast, or ground truth data onto the Allora network. It covers every way to configure and run workers using the Python SDK, from a quick notebook experiment to a production Docker deployment.

## Table of Contents

- [Which role are you?](#which-role-are-you)
- [Choose how to run](#choose-how-to-run)
- [Quick start: Python script](#quick-start-python-script)
  - [Inferer](#inferer)
  - [Forecaster](#forecaster)
  - [Reputer](#reputer)
- [Quick start: Notebook (Jupyter / Colab)](#quick-start-notebook-jupyter--colab)
- [Production: YAML config + CLI](#production-yaml-config--cli)
  - [Config file structure](#config-file-structure)
  - [Wallet settings](#wallet-settings)
  - [Network settings](#network-settings)
  - [Worker entries](#worker-entries)
  - [Running the CLI](#running-the-cli)
- [Production: Docker](#production-docker)
  - [All-in-one container](#all-in-one-container)
  - [Inferer with a model sidecar](#inferer-with-a-model-sidecar)
  - [Reputer with ground truth and loss sidecars](#reputer-with-ground-truth-and-loss-sidecars)
- [Providing your data: Python function vs HTTP API](#providing-your-data-python-function-vs-http-api)
  - [Option A: Python function (default)](#option-a-python-function-default)
  - [Option B: HTTP API sidecar](#option-b-http-api-sidecar)
- [Tuning and options](#tuning-and-options)
  - [Fee tiers](#fee-tiers)
  - [Polling interval](#polling-interval)
  - [Auto-staking rewards](#auto-staking-rewards)
  - [Sanity check (inferer only)](#sanity-check-inferer-only)
  - [Loss functions (reputer only)](#loss-functions-reputer-only)
- [Wallet setup](#wallet-setup)
- [Network reference](#network-reference)
- [Migrating from allora-offchain-node](#migrating-from-allora-offchain-node)

---

## Which role are you?

The Allora network has three participant roles. Pick the one that matches what you want to do:

| Role | You provide | Your goal |
|------|------------|-----------|
| **Inferer** | A prediction value (e.g. a price forecast) | Submit ML model predictions to a topic |
| **Forecaster** | Predictions about how well each inferer will perform | Predict which inferers will be most accurate |
| **Reputer** | A ground truth value (the actual outcome) | Evaluate inference quality and earn rewards |

Each role is configured and run the same way — only the function you write differs.

---

## Choose how to run

| Method | Best for | What you write |
|--------|----------|---------------|
| **Python script** | Getting started, simple setups | A `.py` file with your model function |
| **Notebook** | Experimentation, ML workflows | A notebook cell |
| **YAML config + CLI** | Production, multiple topics/roles | A config file + your model as a Python module |
| **Docker** | Production with model sidecars | A config file + docker-compose |

All four methods use the same SDK underneath. Pick whichever fits your workflow — you can always switch later.

---

## Quick start: Python script

Install the SDK:

```bash
pip install allora_sdk
```

### Inferer
An inferer submits a prediction value each epoch. Your function receives a `nonce` (the block height for this epoch) and returns a number.

```python
import asyncio
import logging
from allora_sdk import AlloraWorker, AlloraWalletConfig, AlloraNetworkConfig

logging.basicConfig(level=logging.INFO)

def predict(nonce: int) -> float:
    # Replace this with your model's prediction logic
    return 3521.50

async def main():
    worker = AlloraWorker.inferer(
        topic_id=22,
        wallet=AlloraWalletConfig(mnemonic="your twelve word mnemonic ..."),
        network=AlloraNetworkConfig.testnet(),
        run=predict,
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logging.error("Error: %s", result)
            continue
        print(f"Submitted prediction: {result.submission}")

asyncio.run(main())
```

Your function can be **sync or async** — both work:

```python
async def predict(nonce: int) -> float:
    price = await my_async_model.infer()
    return price
```

### Forecaster

A forecaster predicts how accurate each inferer on the topic will be. Your function returns a dictionary mapping inferer addresses to predicted values.

```python
def forecast(nonce: int) -> dict[str, float]:
    return {
        "allo1abc...": 3500.0,    # your predicted value for inferer A
        "allo1def...": 3510.5,    # your predicted value for inferer B
    }

async def main():
    worker = AlloraWorker.forecaster(
        topic_id=22,
        wallet=AlloraWalletConfig(mnemonic="..."),
        network=AlloraNetworkConfig.testnet(),
        run=forecast,
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logging.error("Error: %s", result)
            continue
        print(f"Submitted forecast: {result.submission}")

asyncio.run(main())
```

### Reputer

A reputer provides ground truth (the real-world outcome) so the network can evaluate inference quality. The SDK handles fetching predictions, computing losses, staking, and submitting the loss bundle — you just supply the ground truth value.

```python
def get_ground_truth(nonce: int) -> float:
    # Return the actual value for this epoch
    # (e.g. the real ETH price at the time the prediction was about)
    return 3519.88

async def main():
    worker = AlloraWorker.reputer(
        topic_id=22,
        wallet=AlloraWalletConfig(mnemonic="..."),
        network=AlloraNetworkConfig.testnet(),
        ground_truth_fn=get_ground_truth,
        min_stake_uallo=100_000_000,  # 0.1 ALLO minimum stake
    )

    async for result in worker.run():
        if isinstance(result, Exception):
            logging.error("Error: %s", result)
            continue
        print(f"Submitted reputer payload: {result.submission}")

asyncio.run(main())
```

The loss function is auto-selected from the topic's on-chain configuration by default. See [Loss functions](#loss-functions-reputer-only) if you need to customize it.

---

## Quick start: Notebook (Jupyter / Colab)

The SDK auto-detects notebook environments. Use `await` instead of `asyncio.run()`:

```python
# Cell 1: define your model
def predict(nonce: int) -> float:
    return 3521.50

# Cell 2: run the worker
from allora_sdk import AlloraWorker, AlloraWalletConfig, AlloraNetworkConfig

worker = AlloraWorker.inferer(
    topic_id=22,
    wallet=AlloraWalletConfig(mnemonic="..."),
    network=AlloraNetworkConfig.testnet(),
    run=predict,
)

# Use timeout= to avoid running forever in a notebook
async for result in worker.run(timeout=300):  # stop after 5 minutes
    if isinstance(result, Exception):
        print(f"Error: {result}")
        continue
    print(f"Submitted: {result.submission}")
```

To stop the worker manually from another cell:

```python
worker.stop()
```

---

## Production: YAML config + CLI

For production deployments, define all your workers in a single YAML config file and run them with the `allora-worker` CLI. One wallet can serve multiple roles across multiple topics.

### Config file structure

A config file has three top-level sections:

```yaml
wallet:
  # How to sign transactions (one shared wallet for all workers)

network:
  # Which chain to connect to

workers:
  # List of roles to run (at least one required)
```

### Wallet settings

Provide credentials using exactly one of these:

```yaml
wallet:
  # From a file (recommended for production / Docker secrets)
  mnemonic_file: /run/secrets/allora_mnemonic

  # Or from a mnemonic string directly (less secure)
  # mnemonic: "your twelve word mnemonic phrase goes here ..."

  # Or from a hex-encoded private key
  # private_key: "abcdef0123456789..."

  prefix: allo   # default, rarely needs changing
```

### Network settings

```yaml
# Testnet
network:
  chain_id: allora-testnet-1
  url: grpc+https://allora-grpc.testnet.allora.network:443
  websocket_url: wss://allora-rpc.testnet.allora.network/websocket
  fee_denom: uallo
  fee_minimum_gas_price: 10

# Mainnet
# network:
#   chain_id: allora-mainnet-1
#   url: grpc+https://allora-grpc.mainnet.allora.network:443
#   websocket_url: wss://allora-rpc.mainnet.allora.network/websocket
#   fee_denom: uallo
#   fee_minimum_gas_price: 250000000
```

### Worker entries

Each entry in the `workers` list defines one role on one topic. Every role has a **source block** that tells the worker where to get its data — either from a Python function (`type: entrypoint`) or an HTTP API (`type: api`).

**Inferer (Python function):**

```yaml
workers:
  - role: inferer
    topic_id: 22
    inference_source:
      type: entrypoint
      ref: app.inference.eth_model:predict
```

**Inferer (HTTP API):**

```yaml
workers:
  - role: inferer
    topic_id: 22
    inference_source:
      type: api
      url: http://model-api:8000/inference?block={nonce}
      method: GET
      response_field: value
```

**Forecaster (Python function):**

```yaml
workers:
  - role: forecaster
    topic_id: 22
    forecast_source:
      type: entrypoint
      ref: app.forecasts.eth_forecaster:run
```

**Forecaster (HTTP API):**

```yaml
workers:
  - role: forecaster
    topic_id: 22
    forecast_source:
      type: api
      url: http://forecast-api:8000/forecast
      method: POST
      response_field: forecasts
      payload_template:
        block_height: "{nonce}"
```

**Reputer:**

```yaml
workers:
  - role: reputer
    topic_id: 22
    ground_truth_source:
      type: entrypoint
      ref: app.gt.eth_ground_truth:get_value
    min_stake_uallo: 100000000
    loss_function:
      mode: internal_auto
```

**All three in one config:**

```yaml
workers:
  - role: inferer
    topic_id: 22
    inference_source:
      type: entrypoint
      ref: app.inference.eth_model:predict

  - role: forecaster
    topic_id: 22
    forecast_source:
      type: entrypoint
      ref: app.forecasts.eth_forecaster:run

  - role: reputer
    topic_id: 22
    ground_truth_source:
      type: entrypoint
      ref: app.gt.eth_ground_truth:get_value
    min_stake_uallo: 100000000
    loss_function:
      mode: internal_auto
```

**Source types:**

| Type | How it works | `ref` / `url` format |
|------|-------------|---------------------|
| `entrypoint` | Imports a Python callable | `module.path:function_name` |
| `api` | Calls an HTTP endpoint | Full URL, supports `{nonce}` placeholder |

**API source options:**

| Field | Default | Description |
|-------|---------|-------------|
| `url` | *(required)* | API endpoint URL. `{nonce}` is replaced with the block height. |
| `method` | `GET` | HTTP method (`GET` or `POST`) |
| `response_field` | role-specific | JSON key to extract from the response. Defaults to `value` for inferer and reputer sources, and `forecasts` for forecaster sources. |
| `headers` | `{}` | Extra HTTP headers |
| `timeout_seconds` | `10` | Per-request timeout |
| `payload_template` | — | POST body template. Values may contain `{nonce}`. |

**Common worker options:**

| Field | Default | What it does |
|-------|---------|-------------|
| `topic_id` | *(required)* | The Allora topic to participate in |
| `fee_tier` | `standard` | Transaction priority: `eco`, `standard`, or `priority` |
| `polling_interval` | `120` | Seconds between checks for new submission windows |
| `max_unfulfilled_nonces` | `10` | Maximum epochs to process per cycle |
| `debug` | `false` | Verbose logging for this worker |

### Running the CLI

```bash
# Validate your config without connecting to the chain
allora-worker validate --config ./worker_config.yaml

# Run all configured workers
allora-worker run --config ./worker_config.yaml

# Run with debug logging
allora-worker run --config ./worker_config.yaml --debug
```

Press Ctrl-C once for graceful shutdown, twice to force exit.

---

## Production: Docker

Ready-to-run Docker examples are in the [`examples/`](../examples/) folder. Each example is self-contained — `cd` into it and run `docker compose up`.

| Example | What it does |
|---------|-------------|
| [`examples/inferer-local/`](../examples/inferer-local/) | Inferer with a local Python function |
| [`examples/inferer-api/`](../examples/inferer-api/) | Inferer with a sidecar model HTTP service |
| [`examples/forecaster-local/`](../examples/forecaster-local/) | Forecaster with a local Python function |
| [`examples/reputer-local/`](../examples/reputer-local/) | Reputer with a local ground truth function |
| [`examples/reputer-api/`](../examples/reputer-api/) | Reputer with sidecar ground truth + external loss |
| [`examples/multi-worker/`](../examples/multi-worker/) | All three roles in one config |

### Running any example

```bash
# 1. Go to the example
cd examples/inferer-local/

# 2. Create your wallet secret (once)
mkdir -p secrets
echo "your twelve word mnemonic phrase here" > secrets/allora_mnemonic
chmod 600 secrets/allora_mnemonic

# 3. Build the SDK image (once, from repo root)
docker build -t allora-sdk-worker:latest ../..

# 4. Run
docker compose up -d
docker compose logs -f allora-worker
```

### Inferer with a model sidecar

When your inference model runs as a separate HTTP service (FastAPI, TF Serving, custom container, etc.), use [`examples/inferer-api/`](../examples/inferer-api/). The worker calls your model over HTTP — no Python wrapper code needed:

```yaml
inference_source:
  type: api
  url: http://model-api:8000/inference?block={nonce}
  response_field: value
```

### Reputer with ground truth and loss sidecars

For reputers that need external ground truth and/or loss computation services, use [`examples/reputer-api/`](../examples/reputer-api/). The worker calls the ground truth API directly:

```yaml
ground_truth_source:
  type: api
  url: http://ground-truth-api:8001/truth?block={nonce}
  response_field: value
```

The loss function sidecar is configured separately in the same YAML entry:

```yaml
loss_function:
  mode: external_service
  endpoint: http://loss-function-api:8002/loss
  method: POST
  timeout_seconds: 5
  payload_template:
    topic_id: "{topic_id}"
    ground_truth: "{ground_truth}"
    predicted: "{predicted}"
```

You can skip the loss sidecar entirely by using `mode: internal_auto` — the SDK will compute loss internally based on the topic's on-chain configuration.

---

## Providing your data: Python function vs HTTP API

Every worker needs data — your prediction, your forecast, or your ground truth value. There are two ways to provide it, configured via the `*_source` block.

### Option A: Python function (`type: entrypoint`)

Write a function that computes the value directly. This is the simplest approach and works everywhere (scripts, notebooks, YAML runner, Docker).

**What your function looks like per role:**

| Role | Signature | Returns |
|------|-----------|---------|
| Inferer | `(nonce: int) -> str \| float \| Decimal` | A single prediction value |
| Forecaster | `(nonce: int) -> dict[str, float]` | `{inferer_address: predicted_value}` |
| Reputer | `(nonce: int) -> str \| float \| Decimal` | The ground truth value |

The `nonce` is the block height for the current epoch. Your function can be sync or async.

**Python script usage:**

```python
worker = AlloraWorker.inferer(run=my_predict_function, ...)
```

**YAML config usage:**

```yaml
inference_source:
  type: entrypoint
  ref: my_package.my_module:my_function
```

The `ref` format is `module.path:function_name`. The runner imports the module and uses the named function.

### Option B: HTTP API (`type: api`)

When your model runs as a separate HTTP service (a different container, a remote endpoint, a GPU server, etc.), point the source directly at the API — no Python wrapper needed.

**YAML config:**

```yaml
workers:
  - role: inferer
    topic_id: 22
    inference_source:
      type: api
      url: http://model-api:8000/inference?block={nonce}
      response_field: value
```

The `{nonce}` placeholder is replaced with the block height at call time. The SDK handles the HTTP call and extracts the value from the JSON response.

**Python script usage (without YAML):**

If you prefer to use the Python API directly, use the convenience factory functions:

```python
from allora_sdk import AlloraWorker, APISourceConfig, make_api_inferer_fn

run_fn = make_api_inferer_fn(
    APISourceConfig(url="http://model-api:8000/inference?block={nonce}")
)

worker = AlloraWorker.inferer(run=run_fn, ...)
```

Similarly for other roles:

```python
from allora_sdk import make_api_forecaster_fn, make_api_ground_truth_fn

forecast_fn = make_api_forecaster_fn(
    APISourceConfig(url="http://forecast-api:8000/forecast?block={nonce}")
)

ground_truth_fn = make_api_ground_truth_fn(
    APISourceConfig(url="http://truth-api:8001/ground-truth?block={nonce}")
)
```

**Expected API response formats:**

Your HTTP service should return JSON:

```json
// Inferer / ground truth: a scalar
{"value": "3521.50"}

// Forecaster: an address-to-value mapping
{"forecasts": {"allo1abc...": 3500.0, "allo1def...": 3510.5}}
```

---

## Tuning and options

### Fee tiers

Controls how much gas the worker pays per transaction. Higher tiers get included faster.

| Tier | Multiplier | When to use |
|------|-----------|-------------|
| `eco` | 1.0x | Cost-sensitive, low competition |
| `standard` | 1.5x | Default, reliable |
| `priority` | 2.5x | Competitive topics, time-sensitive |

**Python:**

```python
from allora_sdk import FeeTier

worker = AlloraWorker.inferer(fee_tier=FeeTier.PRIORITY, ...)
```

**YAML:**

```yaml
fee_tier: priority
```

### Polling interval

How often (in seconds) the worker checks for new submission windows. The default is 120 seconds. Shorter intervals react faster but use more RPC calls.

**Python:**

```python
worker = AlloraWorker.inferer(polling_interval=60, ...)
```

**YAML:**

```yaml
polling_interval: 60
```

The worker also listens for WebSocket events, so it will react to new epochs in real-time even between polls.

### Auto-staking rewards

Automatically re-stake earned rewards after each reward distribution. Available for inferers and forecasters.

**Python:**

```python
from allora_sdk.worker import AutoStakeConfig, AutoStakeTargetType

worker = AlloraWorker.inferer(
    autostake=AutoStakeConfig(
        target_type=AutoStakeTargetType.REPUTER,       # or VALIDATOR
        target_address="allo1reputer_address...",
        fee_reserve_uallo=1_000_000,  # keep this much for gas fees
    ),
    ...
)
```

| Field | What it does |
|-------|-------------|
| `target_type` | `REPUTER` (delegate to a reputer) or `VALIDATOR` (delegate to a Cosmos validator) |
| `target_address` | The address to stake to |
| `fee_tier` | Override the fee tier for stake transactions (optional, uses worker default) |
| `fee_reserve_uallo` | Amount in uallo to reserve for gas before staking the rest |

### Sanity check (inferer only)

The inferer has a built-in sanity check that compares your prediction against the network consensus. If your value is far off (high z-score), it logs a warning — which can help catch wrong units, wrong target variable, or model bugs.

Enabled by default with a 60-second throttle between RPC calls.

```python
from allora_sdk.worker import SanityCheckConfig

# Customize the throttle interval
worker = AlloraWorker.inferer(
    sanity_check=SanityCheckConfig(throttle_interval_seconds=30.0),
    ...
)

# Or disable entirely
worker = AlloraWorker.inferer(
    sanity_check=SanityCheckConfig(enabled=False),
    ...
)
```

### Loss functions (reputer only)

The reputer computes loss between ground truth and network predictions. There are three ways to configure this:

**1. Auto-select (default)** — the SDK reads the topic's on-chain `loss_method` and uses the matching built-in implementation:

```python
# Python: just omit loss_fn (this is the default)
worker = AlloraWorker.reputer(ground_truth_fn=get_gt, ...)
```

```yaml
# YAML
loss_function:
  mode: internal_auto
```

**2. Named method** — explicitly choose a built-in loss method:

```yaml
loss_function:
  mode: internal_named
  method: ztae
  params:
    std: 0.02
```

Supported methods: `sqe` (squared error), `abse` (absolute error), `huber`, `logcosh`, `bce`, `poisson`, `ztae`, `zptae`.

**3. External HTTP service** — delegate loss computation to a sidecar:

```yaml
loss_function:
  mode: external_service
  endpoint: http://loss-function-api:8002/loss
  method: POST
  timeout_seconds: 5
  payload_template:
    topic_id: "{topic_id}"
    ground_truth: "{ground_truth}"
    predicted: "{predicted}"
```

The `{topic_id}`, `{ground_truth}`, and `{predicted}` placeholders are substituted automatically at runtime. The service must return a numeric value (as JSON or plain text).

**4. Custom Python function:**

```python
def my_loss(ground_truth: float, predicted: float) -> float:
    return abs(ground_truth - predicted)

worker = AlloraWorker.reputer(
    ground_truth_fn=get_gt,
    loss_fn=my_loss,
    ...
)
```

---

## Wallet setup

Your wallet identity is how the network knows you. The SDK can create one for you automatically, or you can provide your own.

**Automatic (easiest for getting started):** If you don't provide a mnemonic, the SDK prompts you to enter one or generates a new one. It saves it to `.allora_key` in the current directory for reuse.

**Explicit (recommended for production):**

| Method | Python | YAML |
|--------|--------|------|
| Mnemonic string | `AlloraWalletConfig(mnemonic="...")` | `mnemonic: "..."` |
| Mnemonic file | `AlloraWalletConfig(mnemonic_file="/path")` | `mnemonic_file: /run/secrets/allora_mnemonic` |
| Private key | `AlloraWalletConfig(private_key="hex...")` | `private_key: "hex..."` |
| Environment vars | `AlloraWalletConfig.from_env()` | -- |

Environment variables: `MNEMONIC`, `MNEMONIC_FILE`, `PRIVATE_KEY`, `ADDRESS_PREFIX`.

> **Security tip:** In Docker deployments, use `mnemonic_file` pointing to a mounted secret rather than putting credentials in the config file or environment variables.

---

## Network reference

| Network | `chain_id` | gRPC URL | Min gas price |
|---------|-----------|----------|---------------|
| Testnet | `allora-testnet-1` | `grpc+https://allora-grpc.testnet.allora.network:443` | `10` |
| Mainnet | `allora-mainnet-1` | `grpc+https://allora-grpc.mainnet.allora.network:443` | `250000000` |
| Local dev | `localnet` | `grpc+http://localhost:9090` | `1` |

**Python shortcuts:**

```python
network = AlloraNetworkConfig.testnet()
network = AlloraNetworkConfig.mainnet()
network = AlloraNetworkConfig.local()
network = AlloraNetworkConfig.from_env()  # reads CHAIN_ID, RPC_ENDPOINT, etc.
```

---

## Migrating from allora-offchain-node

If you're coming from the old `allora-offchain-node`, here's how concepts map:

| allora-offchain-node | This SDK |
|---------------------|----------|
| Multiple service containers + node container | Single `allora-worker` container (+ optional sidecars) |
| `inferenceEntrypointName` | `inference_source` on an inferer worker entry |
| `forecastEntrypointName` | `forecast_source` on a forecaster worker entry |
| `groundTruthEntrypointName` | `ground_truth_source` on a reputer worker entry |
| HTTP model service call (manual) | `inference_source: { type: api, url: ... }` (built-in) |
| External loss function service | `loss_function` with `mode: external_service` |
| `env_file` with wallet credentials | `wallet.mnemonic_file` pointing to a mounted secret |
| One role per container | Multiple roles in one `workers[]` list |

The main simplification: you no longer need a separate "node" container or Python wrapper code for HTTP services. The SDK worker handles chain interaction and API calls directly. Your model containers stay the same — just update the config to point at them.
