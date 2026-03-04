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

- **inferer**: `inference_source` — where to get inference data
- **forecaster**: `forecast_source` — where to get forecast data
- **reputer**: `ground_truth_source`, optional `min_stake_uallo`, optional `loss_function`

### Data sources (`inference_source`, `forecast_source`, `ground_truth_source`)

Each role has a source block that specifies where data comes from. Two types are supported:

#### `type: entrypoint` — Python callable

Resolves a Python callable by import path:

```yaml
inference_source:
  type: entrypoint
  ref: "my_package.models.eth:predict"   # imports my_package.models.eth, calls predict()
```

You can also register named callables programmatically when using the Python API:

```python
from allora_sdk import FunctionRegistry, WorkerManager, WorkerRunnerConfig

registry = FunctionRegistry()
registry.register("my_predictor", predict_fn)

config = WorkerRunnerConfig.from_file("worker_config.yaml")
manager = WorkerManager(config=config, registry=registry)
```

#### `type: api` — HTTP endpoint

Calls an HTTP API and extracts the result from the JSON response:

```yaml
inference_source:
  type: api
  url: http://model-api:8000/inference?block={nonce}
  method: GET
  response_field: value
  timeout_seconds: 10
```

**API source configuration:**

| Field | Default | Description |
|-------|---------|-------------|
| `url` | *(required)* | Endpoint URL. `{nonce}` is replaced with the block height. |
| `method` | `GET` | HTTP method (`GET` or `POST`) |
| `response_field` | `value` | JSON key to extract from the response |
| `headers` | `{}` | Extra HTTP headers |
| `timeout_seconds` | `10` | Per-request timeout |
| `payload_template` | — | POST body template. Values may contain `{nonce}`. |

For POST requests with a custom body:

```yaml
forecast_source:
  type: api
  url: http://forecast-api:8000/forecast
  method: POST
  response_field: forecasts
  payload_template:
    block_height: "{nonce}"
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

Configure the worker to call the model service directly via the `type: api` source:

```yaml
workers:
  - role: inferer
    topic_id: 22
    inference_source:
      type: api
      url: http://model-api:8000/inference?block={nonce}
      response_field: value
```

No Python wrapper code is needed — the SDK handles the HTTP call.

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

Configure the worker to call the ground truth service via `type: api`:

```yaml
workers:
  - role: reputer
    topic_id: 22
    ground_truth_source:
      type: api
      url: http://ground-truth-api:8001/truth?block={nonce}
      response_field: value
    min_stake_uallo: 100000000
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
| `inferenceEntrypointName` | `inference_source` on an `inferer` worker entry |
| `forecastEntrypointName` | `forecast_source` on a `forecaster` worker entry |
| `groundTruthEntrypointName` | `ground_truth_source` on a `reputer` worker entry |
| HTTP model service call (manual) | `inference_source: { type: api, url: ... }` (built-in) |
| External loss function service | `loss_function.mode: external_service` |
| `env_file` with wallet credentials | `wallet.mnemonic_file` pointing to a mounted secret |
| One role per container | Multiple roles in one `workers[]` list |
