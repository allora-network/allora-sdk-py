"""
Z Power-Tanh Absolute Error Loss Function (zptae)

An advanced loss function that applies a power-tanh transformation to z-scores.
This removes the constant loss behavior of regular tanh for extreme values and
adds an MSE penalty term.

Formula:
    z_true = (y_true - mean) / std
    z_pred = (y_pred - mean) / std
    pt_true = power_tanh(z_true, alpha, beta)
    pt_pred = power_tanh(z_pred, alpha, beta)
    main_term = |pt_true - pt_pred|
    mse_term = (mse_norm * |z_pred - z_true|)^gamma
    loss = main_term + mse_term

Where power_tanh(x) = x / (1 + |x|^beta)^((1 - alpha) / beta)

Note: This loss function requires the standard deviation (std) to be provided.
      The SDK does NOT call external services to fetch std - you must provide it.
"""

import math
from typing import Callable, Optional

# Default parameters from the reference implementation
DEFAULT_ALPHA = 0.25
DEFAULT_BETA = 2.0
DEFAULT_MSE_NORM = 0.01
DEFAULT_GAMMA = 4.0


def _power_tanh(x: float, alpha: float = DEFAULT_ALPHA, beta: float = DEFAULT_BETA) -> float:
    """
    Calculate power-tanh function: x / (1 + |x|^beta)^((1 - alpha) / beta)

    This function provides smoother gradients than regular tanh for extreme values.

    Args:
        x: Input value
        alpha: Alpha parameter controlling the shape (default 0.25)
        beta: Beta parameter controlling the steepness (default 2.0)

    Returns:
        Power-tanh transformed value
    """
    try:
        abs_x = abs(x)
        if abs_x == 0:
            return 0.0

        x_beta = abs_x ** beta
        denominator_base = 1.0 + x_beta
        exponent = (1.0 - alpha) / beta
        denominator = denominator_base ** exponent

        return x / denominator

    except (OverflowError, ValueError):
        # Gracefully handle extreme values
        return 1.0 if x > 0 else -1.0


def make_zptae_loss(
    std: float,
    mean: float = 0.0,
    alpha: float = DEFAULT_ALPHA,
    beta: float = DEFAULT_BETA,
    mse_norm: float = DEFAULT_MSE_NORM,
    gamma: float = DEFAULT_GAMMA,
) -> Callable[[float, float], float]:
    """
    Create a ZPTAE (Z Power-Tanh Absolute Error) loss function.

    ZPTAE is an advanced loss function that:
    1. Standardizes values to z-scores
    2. Applies power-tanh transformation (smoother than regular tanh)
    3. Computes absolute error of transformed values
    4. Adds an MSE penalty term for additional gradient signal

    Args:
        std: Standard deviation for z-score normalization.
             Must be positive. This is typically the historical volatility
             of the asset/metric being predicted.
        mean: Mean for z-score normalization. Default is 0.0, which is
              appropriate for return forecasting.
        alpha: Alpha parameter for power-tanh (default 0.25).
               Controls the shape of the transformation.
        beta: Beta parameter for power-tanh (default 2.0).
              Controls the steepness of the transformation.
        mse_norm: MSE normalization parameter (default 0.01).
                  Scales the MSE penalty term.
        gamma: Gamma parameter for MSE term (default 4.0).
               Controls the exponent of the MSE penalty.

    Returns:
        A loss function with signature (ground_truth, predicted) -> float

    Raises:
        ValueError: If std is not positive

    Example:
        >>> zptae = make_zptae_loss(std=0.02, mean=0.0)
        >>> zptae(0.01, 0.02)  # Returns loss value
    """
    if std <= 0:
        raise ValueError(f"std must be positive, got {std}")

    def _zptae_loss(ground_truth: float, predicted: float) -> float:
        # Calculate z-scores
        z_true = (ground_truth - mean) / std
        z_pred = (predicted - mean) / std

        # Apply power-tanh transformation
        pt_true = _power_tanh(z_true, alpha, beta)
        pt_pred = _power_tanh(z_pred, alpha, beta)

        # Calculate the main loss term
        main_term = abs(pt_true - pt_pred)

        # Calculate the MSE penalty term
        mse_term = (mse_norm * abs(z_pred - z_true)) ** gamma

        return main_term + mse_term

    return _zptae_loss


# Default ZPTAE loss with std=1.0 (identity z-score)
# Users should create their own with appropriate std using make_zptae_loss()
zptae_loss = make_zptae_loss(std=1.0, mean=0.0)
zptae_loss.__doc__ = """
Default ZPTAE loss function with std=1.0.

Note: For proper use, create a customized loss function using make_zptae_loss()
with the appropriate standard deviation for your data.

Args:
    ground_truth: The true/actual value
    predicted: The predicted value

Returns:
    The Z Power-Tanh Absolute Error plus MSE penalty term

Example:
    >>> # For custom std, use make_zptae_loss instead:
    >>> from allora_sdk.loss_methods import make_zptae_loss
    >>> custom_zptae = make_zptae_loss(std=0.02, alpha=0.25, beta=2.0)
    >>> loss = custom_zptae(0.01, 0.02)
"""

