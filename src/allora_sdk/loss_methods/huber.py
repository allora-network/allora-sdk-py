"""
Huber Loss Function

A loss function that is quadratic for small errors and linear for large errors,
making it more robust to outliers than squared error.

Formula:
    If |error| <= delta: 0.5 * error^2
    Else: delta * (|error| - 0.5 * delta)

Where error = y_true - y_pred
"""

from typing import Callable

# Default delta value for Huber loss
DEFAULT_HUBER_DELTA = 1.0


def make_huber_loss(delta: float = DEFAULT_HUBER_DELTA) -> Callable[[float, float], float]:
    """
    Create a Huber loss function with a custom delta parameter.

    Args:
        delta: The threshold at which to switch from quadratic to linear loss.
               Must be positive. Default is 1.0.

    Returns:
        A loss function with signature (ground_truth, predicted) -> float

    Raises:
        ValueError: If delta is not positive

    Examples:
        >>> huber = make_huber_loss(delta=2.0)
        >>> huber(0.0, 1.0)
        0.5
        >>> huber(0.0, 3.0)
        4.0
    """
    if delta <= 0:
        raise ValueError(f"Delta must be positive, got {delta}")

    def _huber_loss(ground_truth: float, predicted: float) -> float:
        error = ground_truth - predicted
        abs_error = abs(error)
        if abs_error <= delta:
            return 0.5 * error ** 2
        else:
            return delta * (abs_error - 0.5 * delta)

    return _huber_loss


# Default Huber loss function with delta=1.0
huber_loss = make_huber_loss(DEFAULT_HUBER_DELTA)
huber_loss.__doc__ = """
Calculate Huber loss with the default delta=1.0.

Args:
    ground_truth: The true/actual value
    predicted: The predicted value

Returns:
    The Huber loss value

Examples:
    >>> huber_loss(0.0, 1.0)
    0.5
    >>> huber_loss(0.0, 2.0)
    1.5
    >>> huber_loss(1.0, 2.0)
    0.5
"""

