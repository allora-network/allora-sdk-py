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

See the [`examples/`](../examples/) folder for complete, ready-to-run configs. Top-level sections:

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
| `response_field` | role-specific | JSON key to extract from the response. Defaults to `value` for inferer and reputer sources, and `forecasts` for forecaster sources. |
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

Ready-to-run Docker examples are in the [`examples/`](../examples/) folder. Each one is self-contained with its own `docker-compose.yaml` and `worker_config.yaml`.

### Quick start

```bash
# Pick an example (e.g. inferer with local Python function)
cd examples/inferer-local/

# Create your wallet secret
mkdir -p secrets
echo "your twelve word mnemonic phrase goes here" > secrets/allora_mnemonic
chmod 600 secrets/allora_mnemonic

# Build and run
docker build -t allora-sdk-worker:latest ../..
docker compose up -d
docker compose logs -f allora-worker
```

### Available examples

| Example | Description |
|---------|-------------|
| [`examples/inferer-local/`](../examples/inferer-local/) | Single container, local Python inference function |
| [`examples/inferer-api/`](../examples/inferer-api/) | Worker + model sidecar (HTTP API) |
| [`examples/forecaster-local/`](../examples/forecaster-local/) | Single container, local Python forecast function |
| [`examples/reputer-local/`](../examples/reputer-local/) | Single container, local ground truth function + internal loss |
| [`examples/reputer-api/`](../examples/reputer-api/) | Worker + ground truth sidecar + external loss sidecar |
| [`examples/multi-worker/`](../examples/multi-worker/) | All three roles in one config, mixing local + API sources |

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
