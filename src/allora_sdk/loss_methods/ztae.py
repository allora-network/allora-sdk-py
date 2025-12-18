"""
Z-Transformed Absolute Error Loss Function (ztae)

Computes the absolute error between tanh-transformed z-scores of ground truth
and predicted values. This loss function is useful for return forecasting where
values need to be standardized and bounded.

Formula:
    z_true = (y_true - mean) / std
    z_pred = (y_pred - mean) / std
    loss = |tanh(z_true) - tanh(z_pred)|

Note: This loss function requires the standard deviation (std) to be provided.
      The SDK does NOT call external services to fetch std - you must provide it.
"""

import math
from typing import Callable, Optional


def _tanh(x: float) -> float:
    """
    Calculate tanh(x) with overflow handling.

    For very large values, tanh approaches 1 or -1.
    """
    try:
        return math.tanh(x)
    except OverflowError:
        return 1.0 if x > 0 else -1.0


def make_ztae_loss(
    std: float,
    mean: float = 0.0,
) -> Callable[[float, float], float]:
    """
    Create a ZTAE (Z-Transformed Absolute Error) loss function.

    ZTAE standardizes values to z-scores and applies tanh transformation
    before computing the absolute error. This bounds the error and makes
    it robust to outliers.

    Args:
        std: Standard deviation for z-score normalization.
             Must be positive. This is typically the historical volatility
             of the asset/metric being predicted.
        mean: Mean for z-score normalization. Default is 0.0, which is
              appropriate for return forecasting.

    Returns:
        A loss function with signature (ground_truth, predicted) -> float

    Raises:
        ValueError: If std is not positive

    Example:
        >>> ztae = make_ztae_loss(std=0.02, mean=0.0)
        >>> ztae(0.01, 0.02)  # Returns ~0.245
    """
    if std <= 0:
        raise ValueError(f"std must be positive, got {std}")

    def _ztae_loss(ground_truth: float, predicted: float) -> float:
        # Calculate z-scores
        z_true = (ground_truth - mean) / std
        z_pred = (predicted - mean) / std

        # Apply tanh transformation
        tanh_true = _tanh(z_true)
        tanh_pred = _tanh(z_pred)

        # Return absolute error
        return abs(tanh_true - tanh_pred)

    return _ztae_loss


# Default ZTAE loss with std=1.0 (identity z-score)
# Users should create their own with appropriate std using make_ztae_loss()
ztae_loss = make_ztae_loss(std=1.0, mean=0.0)
ztae_loss.__doc__ = """
Default ZTAE loss function with std=1.0.

Note: For proper use, create a customized loss function using make_ztae_loss()
with the appropriate standard deviation for your data.

Args:
    ground_truth: The true/actual value
    predicted: The predicted value

Returns:
    The Z-Transformed Absolute Error: |tanh(z_true) - tanh(z_pred)|

Example:
    >>> # For custom std, use make_ztae_loss instead:
    >>> from allora_sdk.loss_methods import make_ztae_loss
    >>> custom_ztae = make_ztae_loss(std=0.02)
    >>> loss = custom_ztae(0.01, 0.02)
"""

