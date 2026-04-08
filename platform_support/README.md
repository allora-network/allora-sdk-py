# Allora Platform Support

This directory contains internal platform components for deploying and running user model bundles. These files are not part of the public SDK and will be moved to an internal platform repository.

## Overview

The platform workflow:

1. **User creates bundle** using `allora-bundle` CLI
2. **User uploads** `model-name-version.tar.gz` to platform API
3. **Platform stores** bundle metadata in Postgres database
4. **Kubernetes operator** detects database change and reconciles
5. **Build pipeline** creates container image from bundle + bootstrap script
6. **Deployment** runs container with environment variables for secrets/config

## Components

### `bootstrap.py`

The runtime entrypoint that runs inside each model container. It:
- Reads `model.yaml` from the bundle
- Configures Python path and working directory
- Imports the user's prediction function
- Creates and runs `AlloraWorker.inferer()` with proper configuration
- Handles both sync and async user functions

**Environment Variables:**
- `ALLORA_MNEMONIC` or `ALLORA_PRIVATE_KEY`: Wallet credentials (required)
- `ALLORA_API_KEY`: API key for testnet faucet (optional)
- `ALLORA_TOPIC_ID`: Override topic_id from model.yaml (optional)
- `ALLORA_NETWORK`: Override network ("testnet", "mainnet", or RPC URL) (optional)
- `ALLORA_FEE_TIER`: Override fee tier ("ECO", "STANDARD", "PRIORITY") (optional)
- `ALLORA_POLLING_INTERVAL`: Override polling interval in seconds (optional)
- `BUNDLE_ROOT`: Path to extracted bundle (default: `/workspace/bundle`)
- `DEBUG`: Enable debug logging (optional)

### `Dockerfile`

Template for building container images from user bundles.

**Template Variables** (substituted by platform build pipeline):
- `{{PYTHON_VERSION}}`: From `model.yaml` (e.g., "3.11")
- `{{BUNDLE_ARCHIVE}}`: Path to user's `.tar.gz` file
- `{{BUNDLE_NAME}}`: Extracted directory name

**Build Process:**
1. Use slim Python base image matching user's version
2. Install common system dependencies (build tools, git, curl)
3. Extract user's bundle to `/workspace/`
4. Install Allora SDK
5. Install user's requirements from bundle
6. Copy bootstrap script
7. Set non-root user for security

### `model.yaml`

Example bundle metadata file showing the complete schema. This is the contract between user bundles and the platform.

**Key Fields:**
- `entrypoint.module`: Importable Python module (e.g., `"src.my_pkg.runner"`)
- `entrypoint.function`: Function name (must accept `nonce: int`, return `float | str | Decimal`)
- `python.version`: Required Python version
- `environment.pip_requirements`: Path to requirements.txt
- `allora.*`: Optional defaults for network config (overridden by env vars)

## Platform Integration

### Database Schema

The platform Postgres database should track:
- `bundle_id`: Unique identifier for the bundle
- `bundle_archive_url`: Storage location of the `.tar.gz` file
- `model_name`: From `model.yaml`
- `model_version`: From `model.yaml`
- `python_version`: From `model.yaml`
- `status`: `pending`, `building`, `ready`, `failed`
- `created_at`, `updated_at`: Timestamps

### Kubernetes Custom Resource

The operator should watch for `AlloraModelDeployment` CRs like:

```yaml
apiVersion: allora.network/v1
kind: AlloraModelDeployment
metadata:
  name: my-model-v1-0-0
spec:
  bundleId: "abc123"
  bundleArchiveUrl: "s3://bundles/my-model-1.0.0.tar.gz"
  pythonVersion: "3.11"

  # Secrets injected as env vars
  walletSecretRef:
    name: user-wallet-secret
    key: mnemonic

  # Override model.yaml defaults
  topicId: 69
  network: "testnet"
  feeTier: "STANDARD"

  # Resource limits
  resources:
    requests:
      memory: "2Gi"
      cpu: "1000m"
    limits:
      memory: "4Gi"
      cpu: "2000m"
```

### Reconciliation Loop

The operator should:

1. **Watch database** for new/updated bundle records
2. **Build image** using Dockerfile template + bundle
3. **Create/update deployment** with proper env vars and secrets
4. **Update database status** based on deployment health
5. **Handle failures** with retry logic and user notifications

### Security Considerations

**Critical:**
- Never store wallet keys in database or bundle
- Use Kubernetes secrets for wallet credentials
- Mount secrets as environment variables at runtime
- Run containers as non-root user (UID 1000)
- Use read-only bundle mounts where possible
- Restrict network egress (allow only necessary RPC endpoints)

**Network Policies:**
- Allow outbound: Allora RPC nodes, faucet endpoints
- Deny outbound: Everything else by default
- Consider per-deployment network policies for custom needs

**Resource Limits:**
- Enforce memory/CPU limits from `model.yaml` (with platform maximums)
- Add timeout for prediction execution (default 25s)
- Implement pod disruption budgets for availability

### Build Pipeline

**Recommended flow:**

1. **Download bundle** from storage URL
2. **Extract and validate** `model.yaml`
3. **Build image** with Dockerfile + bundle
   ```bash
   docker build \
     --build-arg PYTHON_VERSION=3.11 \
     --build-arg BUNDLE_ARCHIVE=./my-model-1.0.0.tar.gz \
     --build-arg BUNDLE_NAME=my-model \
     -t my-model:1.0.0 \
     -f Dockerfile .
   ```
4. **Push to registry** (internal container registry)
5. **Update CR** with image reference
6. **Trigger reconciliation**

### Monitoring & Observability

**Metrics to track:**
- Submission success/failure rate
- Prediction latency
- Container resource usage (CPU, memory)
- Transaction costs (gas fees)
- Error types and frequency

**Logs to capture:**
- Bootstrap initialization
- User function imports/errors
- Submission results (tx hashes, explorer links)
- Worker lifecycle events (registration, submissions)

**Suggested log aggregation:**
- Forward container logs to centralized logging (ELK, Loki, CloudWatch)
- Tag with `bundle_id`, `model_name`, `model_version`, `topic_id`
- Alert on repeated failures or high error rates

### Deployment Strategy

**Zero-downtime updates:**
- Use rolling updates with readiness probes
- New version deploys alongside old version
- Traffic shifts after health checks pass
- Old version terminated after grace period

**Readiness probe:**
```yaml
readinessProbe:
  exec:
    command:
    - python
    - -c
    - "import allora_sdk; print('ready')"
  initialDelaySeconds: 10
  periodSeconds: 5
```

**Liveness probe:**
```yaml
livenessProbe:
  exec:
    command:
    - python
    - -c
    - "import os; open('/tmp/heartbeat').close()"
  initialDelaySeconds: 60
  periodSeconds: 30
```

### Cost Optimization

**Recommendations:**
- Cache base images (python:3.11-slim, etc.) on nodes
- Use shared wheel cache for common dependencies
- Implement bundle deduplication (same bundle, multiple deployments)
- Scale down idle deployments (no recent submissions)
- Use spot/preemptible instances where appropriate

### Common Issues & Troubleshooting

**Bundle fails to load:**
- Check `model.yaml` syntax with validator
- Verify module path matches actual file structure
- Ensure requirements.txt has all dependencies

**Import errors:**
- Check Python version matches between dev and platform
- Verify PYTHONPATH includes bundle root
- Look for missing system dependencies (need custom Dockerfile)

**Submission failures:**
- Verify wallet has sufficient ALLO balance
- Check network connectivity to RPC nodes
- Review topic whitelist status
- Validate prediction format (must be scalar)

**High latency:**
- Check prediction function execution time
- Review model loading strategy (cache vs reload)
- Consider model quantization or optimization
- Scale horizontally if needed

### Future Enhancements

**Planned improvements:**
- Support for GPU-accelerated models
- Multi-model inference (ensembles)
- A/B testing between model versions
- Automated rollback on high error rates
- Model performance analytics dashboard
- Cost attribution per deployment

**MLflow compatibility path:**
- Add MLmodel file generation from model.yaml
- Support MLflow model flavors (sklearn, pytorch, etc.)
- Enable MLflow tracking integration
- Implement model registry sync

## Testing Locally

You can test the bootstrap script locally:

```bash
# Set up environment
export BUNDLE_ROOT=/path/to/your/bundle
export ALLORA_MNEMONIC="your test mnemonic"
export ALLORA_TOPIC_ID=69
export ALLORA_NETWORK=testnet
export ALLORA_API_KEY=your-api-key

# Run bootstrap
python bootstrap.py
```

Or build and run the Docker image:

```bash
# Build image
docker build \
  --build-arg PYTHON_VERSION=3.11 \
  -t test-model:latest \
  -f Dockerfile .

# Run container
docker run --rm \
  -e ALLORA_MNEMONIC="your test mnemonic" \
  -e ALLORA_TOPIC_ID=69 \
  -e ALLORA_NETWORK=testnet \
  -e ALLORA_API_KEY=your-api-key \
  test-model:latest
```

## Contact

For questions or issues with platform integration, contact the platform team.
