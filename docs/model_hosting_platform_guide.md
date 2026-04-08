# Allora Model Hosting Platform Guide

This guide describes the interface between ML users and the Allora hosting platform. It covers how users package their models, what the platform expects, and how everything fits together at runtime.

## Overview

The Allora model hosting platform allows ML developers to deploy prediction models that submit inferences to Allora blockchain topics. Users package their models as **bundles** using a standard format, and the platform handles all infrastructure, blockchain interaction, and execution.

## User-Facing: The Bundle Format

### What Users Provide

Users create a **model bundle** - a directory containing:

1. **model.yaml** - Metadata and configuration
2. **requirements.txt** - Python dependencies (pinned versions)
3. **src/** - User's Python code
4. **artifacts/** - Model weights, data files, pickles, etc.

### Bundle Structure

```
my-trading-strategy/
├── model.yaml              # Required: Bundle metadata
├── requirements.txt        # Required: Python dependencies
├── src/                    # User's code
│   └── my_strategy/
│       ├── __init__.py
│       └── runner.py       # Contains the prediction function
└── artifacts/              # Model files (weights, data, etc.)
    ├── model.pkl
    ├── scaler.pkl
    └── config.json
```

### model.yaml Schema (v0.1)

```yaml
schema_version: "0.1"

# Model identity
name: "my-trading-strategy"
version: "1.0.0"

# Python runtime
python:
  version: "3.11"  # Supported: 3.10, 3.11, 3.12, 3.13

# Entrypoint: How to call your prediction function
entrypoint:
  kind: "python_function"
  module: "src.my_strategy.runner"     # Importable module path
  function: "run"                       # Function name
  workdir: "."                          # Working directory
  artifacts_dir: "artifacts"            # Location of model files

# Dependencies
environment:
  pip_requirements: "requirements.txt"

# Allora configuration (optional - platform can override)
allora:
  topic_id: 69
  polling_interval: 120
  fee_tier: "STANDARD"
  network: "testnet"
```

### The Prediction Function Contract

Users must implement a function with this signature:

```python
def run(nonce: int) -> float | str | Decimal:
    """
    Generate a prediction for the given nonce (block height).

    Args:
        nonce: The block height for this prediction window

    Returns:
        A scalar prediction value
    """
    # Your model logic here
    return prediction
```

**Key Points:**
- Function accepts exactly one parameter: `nonce` (int)
- Returns a scalar value: float, int, str, or Decimal
- Can be sync or async
- No SDK imports required - pure Python
- Load artifacts relative to bundle root

**Example with artifacts:**

```python
from pathlib import Path
import joblib

# Load model at module import time (cached)
artifacts_dir = Path(__file__).parent.parent.parent / "artifacts"
model = joblib.load(artifacts_dir / "model.pkl")
scaler = joblib.load(artifacts_dir / "scaler.pkl")

def run(nonce: int) -> float:
    """Generate prediction using loaded model."""
    # Transform nonce into features
    features = scaler.transform([[nonce]])

    # Run inference
    prediction = model.predict(features)[0]

    return float(prediction)
```

## User-Facing: The CLI Tool

The `allora-bundle` CLI helps users create, validate, and build bundles.

### Installation

```bash
pip install allora-sdk
```

### Commands

#### `init` - Create New Project

```bash
allora-bundle init my-model
```

Creates a new bundle project with:
- Scaffolded directory structure
- Template model.yaml
- Example runner.py with documentation
- Empty requirements.txt and artifacts/

#### `validate` - Check Bundle

```bash
cd my-model
allora-bundle validate
```

Validates:
- model.yaml schema and required fields
- requirements.txt existence
- Entrypoint module is importable
- Python version is supported

Warns about:
- Unpinned dependencies (should use `==` not `>=`)
- Missing artifacts directory

#### `build` - Create Archive

```bash
allora-bundle build
```

Creates `dist/my-model-1.0.0.tar.gz` ready for upload.

Automatically excludes:
- `.git`, `.gitignore`
- `__pycache__`, `*.pyc`, `*.pyo`
- `.pytest_cache`, `.mypy_cache`, `.tox`
- Virtual environments (`.venv`, `venv/`)
- Environment files (`.env`)
- Build artifacts (`dist/`)

## Platform-Facing: Runtime Execution

### What the Platform Does

1. **Receives bundle** (.tar.gz upload from user)
2. **Stores metadata** in Postgres database
3. **Builds container image** using:
   - Base Python image (version from model.yaml)
   - User's bundle extracted to `/workspace/`
   - Allora SDK installed
   - User's requirements.txt installed
   - Bootstrap script that runs the model
4. **Deploys to Kubernetes** with:
   - Wallet secrets injected as env vars
   - Topic and network configuration
   - Resource limits and health checks
5. **Monitors execution** with logs and metrics

### Bootstrap Process

The platform runs `bootstrap.py` inside each container, which:

1. Loads `model.yaml` configuration
2. Sets up Python path (adds bundle root to sys.path)
3. Changes to working directory
4. Imports user's function dynamically: `importlib.import_module(module).function`
5. Reads configuration from environment variables
6. Creates `AlloraWorker.inferer(run=user_function, ...)`
7. Starts worker loop:
   - Subscribes to blockchain events
   - Waits for submission windows
   - Calls user_function(nonce) for each window
   - Submits prediction to blockchain
   - Logs results

### Environment Variables (Platform → Container)

The platform injects these via Kubernetes secrets/configmaps:

**Required:**
- `ALLORA_MNEMONIC` or `ALLORA_PRIVATE_KEY` - Wallet credentials

**Optional (override model.yaml):**
- `ALLORA_TOPIC_ID` - Topic to submit to
- `ALLORA_NETWORK` - "testnet" | "mainnet" | custom RPC URL
- `ALLORA_FEE_TIER` - "ECO" | "STANDARD" | "PRIORITY"
- `ALLORA_POLLING_INTERVAL` - Seconds between polling
- `ALLORA_API_KEY` - API key for testnet faucet
- `DEBUG` - Enable debug logging

**Internal:**
- `BUNDLE_ROOT` - Path to extracted bundle (default: `/workspace/bundle`)

### Execution Flow

```
User uploads bundle.tar.gz
        ↓
Platform stores in DB
        ↓
K8s operator detects change
        ↓
Build Docker image:
    - Extract bundle
    - Install dependencies
    - Copy bootstrap.py
        ↓
Deploy to K8s with secrets
        ↓
Container starts:
    bootstrap.py runs
        ↓
    Load model.yaml
        ↓
    Import user's module.function
        ↓
    Create AlloraWorker.inferer(run=user_function)
        ↓
    Subscribe to blockchain events
        ↓
    For each submission window:
        Call user_function(nonce)
        Submit to blockchain
        Log result
```

## User Workflow Example

### 1. Create Project

```bash
$ allora-bundle init btc-price-predictor
Creating new bundle: btc-price-predictor
Location: ./btc-price-predictor

✅ Bundle initialized successfully!

Next steps:
  1. cd btc-price-predictor
  2. Edit src/btc_price_predictor/runner.py to implement your model
  3. Add dependencies to requirements.txt
  4. Run 'allora-bundle validate' to check your bundle
  5. Run 'allora-bundle build' to create a deployable archive
```

### 2. Implement Model

Edit `src/btc_price_predictor/runner.py`:

```python
from pathlib import Path
import joblib
import numpy as np

# Load model at import time
artifacts_dir = Path(__file__).parent.parent.parent / "artifacts"
model = joblib.load(artifacts_dir / "xgboost_model.pkl")
feature_scaler = joblib.load(artifacts_dir / "scaler.pkl")

def run(nonce: int) -> float:
    """Predict BTC price for the given block height."""
    # Create features from nonce
    features = np.array([[nonce, nonce % 100, nonce % 1000]])

    # Scale and predict
    features_scaled = feature_scaler.transform(features)
    prediction = model.predict(features_scaled)[0]

    return float(prediction)
```

### 3. Add Dependencies

Edit `requirements.txt`:

```
numpy==1.26.4
scikit-learn==1.3.2
xgboost==2.0.3
joblib==1.3.2
```

### 4. Add Model Files

```bash
# Copy your trained model files
cp ~/my_models/xgboost_model.pkl artifacts/
cp ~/my_models/scaler.pkl artifacts/
```

### 5. Update Configuration

Edit `model.yaml`:

```yaml
allora:
  topic_id: 1  # BTC/USD topic
  polling_interval: 300  # 5 minutes
  network: "mainnet"
```

### 6. Validate

```bash
$ allora-bundle validate
Validating bundle at: .
✓ model.yaml structure is valid
✓ requirements.txt exists
✓ Entrypoint module file found
✓ Artifacts directory exists

✅ Bundle validation passed!
```

### 7. Build

```bash
$ allora-bundle build
Building archive: dist/btc-price-predictor-1.0.0.tar.gz
✅ Bundle built successfully!
   Archive: dist/btc-price-predictor-1.0.0.tar.gz
   Size: 45.32 MB
```

### 8. Upload to Platform

```bash
# Via platform web UI or API
curl -X POST https://platform.allora.network/api/bundles \
  -F "bundle=@dist/btc-price-predictor-1.0.0.tar.gz" \
  -H "Authorization: Bearer YOUR_API_TOKEN"
```

### 9. Configure Deployment

Via platform UI:
- Select wallet (or create new)
- Confirm topic ID and network
- Set resource limits
- Deploy

### 10. Monitor

Platform shows:
- Deployment status (building → running → active)
- Recent submissions (tx hashes, predictions, timestamps)
- Logs from your model
- Resource usage (CPU, memory)
- Success/failure rates

## Platform Integration Details

### Database Schema (Recommended)

```sql
CREATE TABLE model_bundles (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    version VARCHAR(50) NOT NULL,
    archive_url TEXT NOT NULL,
    python_version VARCHAR(10) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(name, version)
);

CREATE TABLE deployments (
    id UUID PRIMARY KEY,
    bundle_id UUID REFERENCES model_bundles(id),
    user_id UUID NOT NULL,
    wallet_id UUID NOT NULL,
    topic_id INTEGER NOT NULL,
    network VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL,  -- pending, building, running, failed, stopped
    container_image TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE submission_results (
    id UUID PRIMARY KEY,
    deployment_id UUID REFERENCES deployments(id),
    nonce BIGINT NOT NULL,
    prediction TEXT NOT NULL,
    tx_hash VARCHAR(100),
    status VARCHAR(50) NOT NULL,  -- success, error, timeout
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Kubernetes CRD Example

```yaml
apiVersion: allora.network/v1
kind: AlloraModelDeployment
metadata:
  name: btc-price-predictor-v1-0-0
  namespace: allora-models
spec:
  # Bundle information
  bundleId: "abc123-def456"
  bundleArchiveUrl: "s3://allora-bundles/btc-price-predictor-1.0.0.tar.gz"
  pythonVersion: "3.11"

  # Wallet secrets (injected as env vars)
  walletSecretRef:
    name: user-wallet-btc-predictor
    key: mnemonic

  # Allora configuration
  topicId: 1
  network: "mainnet"
  feeTier: "STANDARD"
  pollingInterval: 300

  # Resource allocation
  resources:
    requests:
      memory: "2Gi"
      cpu: "1000m"
    limits:
      memory: "4Gi"
      cpu: "2000m"

  # Monitoring
  monitoring:
    enabled: true
    metricsPort: 9090
```

### Security Considerations

**Secrets Management:**
- Never store wallet keys in bundles or database
- Use Kubernetes secrets for wallet credentials
- Rotate secrets regularly
- Audit secret access

**Network Policies:**
- Allow outbound: Allora RPC endpoints only
- Deny all other egress by default
- No ingress (models don't expose ports)

**Resource Limits:**
- Enforce CPU/memory limits from model.yaml
- Set maximum bundle size (e.g., 500MB)
- Timeout prediction execution (default 25s)
- Limit concurrent deployments per user

**Container Security:**
- Run as non-root user (UID 1000)
- Read-only root filesystem where possible
- No privileged containers
- Scan images for vulnerabilities

### Monitoring & Observability

**Metrics to Collect:**
- Submission success/failure rate per deployment
- Prediction latency (p50, p95, p99)
- Container resource usage
- Transaction costs (gas fees)
- Error types and frequency

**Logs to Aggregate:**
- Bootstrap initialization
- User function execution
- Submission results
- Worker events (registration, windows)
- Errors and exceptions

**Alerting:**
- Deployment failures
- Repeated submission errors (>3 consecutive)
- High resource usage (>80% of limits)
- Container crashes/restarts

### Build Pipeline Flow

```
1. User uploads bundle.tar.gz via API
    ↓
2. API validates bundle format
    ↓
3. Store archive in object storage (S3/GCS)
    ↓
4. Insert record into model_bundles table
    ↓
5. K8s operator detects DB change
    ↓
6. Build Docker image:
    - Use Dockerfile template
    - Substitute {{PYTHON_VERSION}}, {{BUNDLE_ARCHIVE}}, etc.
    - Build and tag: registry/bundle-abc123:latest
    ↓
7. Push to container registry
    ↓
8. Create/update AlloraModelDeployment CR
    ↓
9. K8s creates deployment with:
    - Image from registry
    - Secrets for wallet
    - ConfigMap for settings
    - Resource limits
    ↓
10. Container starts, runs bootstrap.py
    ↓
11. Model begins submitting predictions
```

## Testing and Development

### Local Testing

Users can test their prediction function locally:

```python
from src.my_model.runner import run

# Test with sample nonces
for nonce in [1000, 2000, 3000]:
    prediction = run(nonce)
    print(f"Nonce {nonce}: {prediction}")
```

### Platform Testing

Platform can test bundles before deployment:

```bash
# Extract and validate
tar -xzf bundle.tar.gz
python3 -m allora_sdk.tools.bundle validate ./bundle-dir/

# Test import
cd bundle-dir
python3 -c "from src.my_model.runner import run; print(run(1000))"
```

### Dry-run Mode

Platform could offer dry-run deployments:
- Run model in sandbox environment
- Use testnet with platform-provided wallet
- Limited time execution (1 hour)
- Free for users to test before mainnet

## Common User Errors & Solutions

### Import Error: Module Not Found

**Problem:** User's module path in model.yaml doesn't match actual file structure.

**Solution:**
- Check that `entrypoint.module` matches the actual import path
- Example: If file is `src/my_model/runner.py`, module should be `src.my_model.runner`
- Ensure `__init__.py` files exist in all package directories

### Requirements Installation Fails

**Problem:** Dependency has version conflict or requires system libraries.

**Solution:**
- Pin exact versions in requirements.txt: `numpy==1.26.4` not `numpy>=1.26`
- If needs system libs (e.g., ffmpeg), contact platform team for custom Dockerfile support
- Test locally: `pip install -r requirements.txt` in clean venv

### Prediction Returns Wrong Type

**Problem:** Function returns list, dict, or None instead of scalar.

**Solution:**
- Must return: float, int, str, or Decimal
- Convert: `return float(prediction)` not `return prediction.tolist()`
- Check: `isinstance(result, (float, int, str, Decimal))`

### Artifacts Not Found

**Problem:** Model files not loading, FileNotFoundError.

**Solution:**
- Use relative paths from bundle root
- Recommended pattern:
  ```python
  artifacts_dir = Path(__file__).parent.parent.parent / "artifacts"
  model = joblib.load(artifacts_dir / "model.pkl")
  ```
- Ensure artifacts/ is in bundle and files are committed

### High Memory Usage / OOM Kills

**Problem:** Container exceeds memory limit and gets killed.

**Solution:**
- Optimize model (quantization, pruning)
- Request higher memory limit in platform UI
- Load model once at import time, not in run() function
- Clear caches after predictions if needed

## Future Enhancements

### Short-term (v0.2)
- Custom Dockerfile support for complex dependencies
- GPU-enabled deployments for ML models
- Bundle versioning and rollback
- A/B testing between versions
- Performance profiling tools

### Long-term (v1.0)
- Full MLflow compatibility (MLmodel files, model flavors)
- Model registry integration
- Automated model optimization
- Multi-model ensembles
- Model marketplace

## Support

**For SDK and Bundle Issues:**
- GitHub: https://github.com/allora-network/allora-sdk-py/issues
- Discord: #ml-models channel

**For Platform Issues:**
- Platform UI: Help → Support
- Email: support@allora.network
