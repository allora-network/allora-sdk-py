# Multi-topic Worker Runner

The SDK includes a config-driven runner that lets one wallet run multiple roles (inferer, forecaster, reputer) across multiple topics in a single containerized process.

## CLI

```bash
# Validate config without connecting
allora-worker validate --config ./worker_config.yaml

# Run all configured workers
allora-worker run --config ./worker_config.yaml

# Debug logging
allora-worker run --config ./worker_config.yaml --debug
```

## Config schema

See `worker_config.example.yaml` for a complete reference. Top-level sections:

- `wallet` – one wallet shared by all workers
- `network` – chain and endpoint settings
- `workers` – list of role entries (at least one required)

Each worker entry requires:

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `role` | yes | — | `inferer`, `forecaster`, or `reputer` |
| `topic_id` | yes | — | positive integer |
| `fee_tier` | no | `standard` | `eco`, `standard`, or `priority` |
| `polling_interval` | no | `120` | seconds between polls |
| `max_unfulfilled_nonces` | no | `10` | cap on concurrent nonces |
| `debug` | no | `false` | verbose logging for this worker |

Role-specific fields:

- **inferer**: `run_ref` — Python callable that returns an inference value
- **forecaster**: `run_ref` — Python callable that returns forecasts
- **reputer**: `ground_truth_ref`, optional `min_stake_uallo`, optional `loss_function`

### Callable references (`run_ref`, `ground_truth_ref`)

The runner resolves Python callables by import path:

```yaml
run_ref: "my_package.models.eth:predict"   # imports my_package.models.eth, calls predict()
```

You can also register named callables programmatically when using the Python API:

```python
from allora_sdk import FunctionRegistry, WorkerManager, WorkerRunnerConfig

registry = FunctionRegistry()
registry.register("my_predictor", predict_fn)

config = WorkerRunnerConfig.from_file("worker_config.yaml")
manager = WorkerManager(config=config, registry=registry)
```

## Reputer loss modes

### `internal_auto` (default)

SDK auto-selects the loss method based on the topic's on-chain `loss_method` field.

```yaml
loss_function:
  mode: internal_auto
```

### `internal_named`

Explicitly set the loss method with optional parameters:

```yaml
loss_function:
  mode: internal_named
  method: ztae
  params:
    std: 0.02
```

Supported methods: `mse`, `mae`, `huber`, `bce`, `poisson`, `ztae`, `zptae`.

### `external_service`

Delegate loss computation to a sidecar HTTP service (e.g. `allora-standard-loss-functions`):

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

The service must return either:
- A JSON object with a `"loss"` or `"value"` numeric field
- A bare numeric value (JSON number or plain text)

## Docker deployment

### Prerequisites

Create a secrets directory with your wallet mnemonic:

```bash
mkdir -p secrets
echo "your twelve word mnemonic phrase goes here" > secrets/allora_mnemonic
chmod 600 secrets/allora_mnemonic
```

### Basic deployment (all roles in one container)

```bash
docker compose -f docker-compose.worker.yaml up -d
docker compose -f docker-compose.worker.yaml logs -f allora-worker
```

This runs every role defined in `worker_config.yaml`. Use this when your inference/forecast/ground-truth functions are pure Python callables bundled in the image.

### Inferer with model sidecar

Use `docker-compose.inferer.yaml` when your inference model runs as a separate service (e.g. a TensorFlow Serving container, a FastAPI model server, etc.):

```bash
docker compose -f docker-compose.inferer.yaml up -d
```

The compose file starts two containers:
- **model-api** — your inference model service on port 8000
- **allora-worker** — the SDK worker that calls the model

Your `run_ref` Python callable should call the model service internally:

```python
# app/inference/eth_model.py
import os, httpx

async def predict() -> str:
    url = os.environ.get("MODEL_API_URL", "http://model-api:8000")
    resp = httpx.get(f"{url}/predict")
    return resp.json()["value"]
```

### Reputer with ground truth + loss function sidecars

Use `docker-compose.reputer.yaml` when you need sidecar services for ground truth and/or loss computation — this mirrors the `allora-offchain-node` pattern:

```bash
docker compose -f docker-compose.reputer.yaml up -d
```

The compose file starts three containers:

| Service | Purpose | Port |
|---------|---------|------|
| **ground-truth-api** | Provides real-world values (prices, outcomes, etc.) | 8001 |
| **loss-function-api** | Computes loss (e.g. `allora-standard-loss-functions`) | 8002 |
| **allora-worker** | SDK worker that calls both services | — |

#### Ground truth sidecar

Your `ground_truth_ref` callable should call the ground truth service:

```python
# app/gt/eth_ground_truth.py
import os, httpx

async def get_value() -> float:
    url = os.environ.get("GROUND_TRUTH_API_URL", "http://ground-truth-api:8001")
    resp = httpx.get(f"{url}/truth")
    return float(resp.json()["value"])
```

#### External loss function sidecar (allora-standard-loss-functions)

When using `allora-standard-loss-functions` or a similar service:

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

The `{topic_id}`, `{ground_truth}`, and `{predicted}` placeholders are automatically substituted at runtime.

#### Using internal loss instead

If you prefer to skip the loss function sidecar and compute loss directly in the SDK:

```yaml
loss_function:
  mode: internal_auto    # auto-select from chain config
```

or:

```yaml
loss_function:
  mode: internal_named
  method: huber
  params:
    delta: 1.0
```

This eliminates the need for the `loss-function-api` container.

### Building the worker image

```bash
docker build -t allora-sdk-worker:latest .
```

To include your own Python modules (models, ground truth callables), either:
1. Mount them as volumes in docker-compose
2. Extend the Dockerfile to copy your code into the image

## Migration from `allora-offchain-node`

| offchain-node concept | SDK equivalent |
|----------------------|----------------|
| Multiple service containers + node container | Single `allora-worker` container (+ optional sidecars) |
| `inferenceEntrypointName` | `run_ref` on an `inferer` worker entry |
| `forecastEntrypointName` | `run_ref` on a `forecaster` worker entry |
| `groundTruthEntrypointName` | `ground_truth_ref` on a `reputer` worker entry |
| External loss function service | `loss_function.mode: external_service` |
| `env_file` with wallet credentials | `wallet.mnemonic_file` pointing to a mounted secret |
| One role per container | Multiple roles in one `workers[]` list |
