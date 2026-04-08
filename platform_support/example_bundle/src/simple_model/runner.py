"""
Simple model example for Allora platform.

This is a minimal working example that demonstrates:
- Basic prediction function signature
- Using numpy for computation
- Loading artifacts (if needed)
"""

import numpy as np


def run(nonce: int) -> float:
    """
    Generate a prediction based on the nonce (block height).

    This is a simple example that uses a sinusoidal function.
    In a real model, you would load weights from artifacts and run inference.

    Args:
        nonce: The block height for this prediction window

    Returns:
        A scalar prediction value
    """
    # Simple sinusoidal prediction based on nonce
    # This is just for demonstration - replace with your actual model logic
    prediction = 100.0 + 10.0 * np.sin(nonce / 100.0)

    return float(prediction)


# Example of how to load artifacts:
#
# from pathlib import Path
# import joblib
#
# # Get artifacts directory
# artifacts_dir = Path(__file__).parent.parent.parent / "artifacts"
#
# # Load model once at module import time (cached)
# model = joblib.load(artifacts_dir / "model.pkl")
#
# def run(nonce: int) -> float:
#     """Generate prediction using loaded model."""
#     features = np.array([[nonce]])
#     prediction = model.predict(features)[0]
#     return float(prediction)
