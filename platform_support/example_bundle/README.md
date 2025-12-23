# Simple Model Example Bundle

This is a minimal working example of an Allora model bundle.

## Structure

```
example_bundle/
├── model.yaml              # Bundle metadata
├── requirements.txt        # Dependencies (numpy==1.26.4)
├── src/
│   └── simple_model/
│       ├── __init__.py
│       └── runner.py       # Prediction function
└── artifacts/              # Empty (no artifacts needed for this example)
```

## What it does

The `run()` function in `runner.py` generates a simple sinusoidal prediction based on the nonce (block height):

```python
prediction = 100.0 + 10.0 * np.sin(nonce / 100.0)
```

This is just for demonstration. A real model would:
1. Load weights/artifacts from the `artifacts/` directory
2. Perform actual ML inference
3. Return predictions

## Testing locally

```python
from src.simple_model.runner import run

# Test the function
result = run(nonce=12345)
print(f"Prediction for nonce 12345: {result}")
```

## Building

From this directory:

```bash
# Validate the bundle
allora-bundle validate

# Build the archive
allora-bundle build

# This creates: dist/simple-model-0.1.0.tar.gz
```

## Deploying

The platform will:
1. Extract the bundle
2. Install requirements
3. Run the bootstrap script with proper environment variables
4. Call your `run()` function for each submission window
