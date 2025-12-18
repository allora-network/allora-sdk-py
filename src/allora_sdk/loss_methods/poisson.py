"""
Poisson Loss Function

Computes the Poisson loss, commonly used for count data and rate predictions.
Both ground truth and predicted values must be non-negative.

Formula: y_pred - y_true * log(y_pred)

Note: The epsilon parameter is used to prevent log(0) when y_pred is 0.
"""

import math
from typing import Callable

# Default epsilon for numerical stability
DEFAULT_POISSON_EPSILON = 1e-7


def make_poisson_loss(epsilon: float = DEFAULT_POISSON_EPSILON) -> Callable[[float, float], float]:
    """
    Create a Poisson loss function with a custom epsilon.

    Args:
        epsilon: Small value to use instead of 0 for y_pred to avoid log(0).
                 Default is 1e-7.

    Returns:
        A loss function with signature (ground_truth, predicted) -> float

    Examples:
        >>> poisson = make_poisson_loss(epsilon=1e-7)
        >>> round(poisson(2.0, 3.0), 6)
        0.802848
    """

    def _poisson_loss(ground_truth: float, predicted: float) -> float:
        # Validate inputs are non-negative
        if ground_truth < 0:
            raise ValueError(f"ground_truth must be non-negative for Poisson loss, got {ground_truth}")
        if predicted < 0:
            raise ValueError(f"predicted must be non-negative for Poisson loss, got {predicted}")

        # Use epsilon if predicted is 0 to avoid log(0)
        y_pred = max(predicted, epsilon)

        # Calculate Poisson loss: y_pred - y_true * log(y_pred)
        return y_pred - ground_truth * math.log(y_pred)

    return _poisson_loss


# Default Poisson loss function
poisson_loss = make_poisson_loss(DEFAULT_POISSON_EPSILON)
poisson_loss.__doc__ = """
Calculate Poisson loss with the default epsilon=1e-7.

Args:
    ground_truth: The true count/rate value (must be non-negative)
    predicted: The predicted count/rate value (must be non-negative)

Returns:
    The Poisson loss: y_pred - y_true * log(y_pred)

Raises:
    ValueError: If ground_truth or predicted is negative

Examples:
    >>> round(poisson_loss(2.0, 3.0), 6)
    0.802848
    >>> round(poisson_loss(5.0, 4.0), 6)
    -2.931472
    >>> round(poisson_loss(0.0, 1.0), 1)
    1.0
"""

