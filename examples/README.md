# Examples

Ready-to-run examples for every worker role and deployment style. Each folder is self-contained — `cd` into it and follow the instructions.

## Which example should I use?

| I want to run a... | My model is... | Use this example |
|---------------------|---------------|------------------|
| **Inferer** | A Python function | [`inferer-local/`](inferer-local/) |
| **Inferer** | A separate HTTP service (container, GPU server, etc.) | [`inferer-api/`](inferer-api/) |
| **Forecaster** | A Python function | [`forecaster-local/`](forecaster-local/) |
| **Reputer** | A Python function for ground truth, SDK computes loss | [`reputer-local/`](reputer-local/) |
| **Reputer** | Separate HTTP services for ground truth + loss | [`reputer-api/`](reputer-api/) |
| **All three** | Mix of Python functions and HTTP services | [`multi-worker/`](multi-worker/) |
| **Quick test** | Just a Python script, no Docker | [`python-scripts/`](python-scripts/) |

## Running a Docker example

Every Docker example follows the same steps:

```bash
# 1. Go to the example folder
cd examples/inferer-local/

# 2. Create your wallet secret (once)
mkdir -p secrets
echo "your twelve word mnemonic phrase here" > secrets/allora_mnemonic
chmod 600 secrets/allora_mnemonic

# 3. Build the SDK worker image (once, from the repo root)
docker build -t allora-sdk-worker:latest ../..

# 4. Run
docker compose up -d
docker compose logs -f allora-worker
```

## Running a Python script example

```bash
cd examples/python-scripts/

# Install the SDK
pip install allora_sdk

# Edit the script to set your mnemonic and topic_id, then run
python inferer.py
```

## Customizing

- **Change the topic**: edit `topic_id` in the example's `worker_config.yaml`.
- **Change the network**: edit the `network` section (see [docs/usage.md](../docs/usage.md#network-reference) for testnet/mainnet settings).
- **Add your model logic**: edit the Python file in `app/` (for `-local` examples) or point `url` at your model service (for `-api` examples).
- **Change the wallet**: replace the mnemonic in `secrets/allora_mnemonic`.

## Folder structure

Each Docker example contains:

```
example-name/
├── docker-compose.yaml      # container orchestration
├── worker_config.yaml        # worker configuration
├── app/                      # your model code (local examples only)
│   └── model.py
└── secrets/                  # created by you, gitignored
    └── allora_mnemonic
```
