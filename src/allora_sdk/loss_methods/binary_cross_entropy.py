"""
Binary Cross Entropy Loss Function (bce)

Computes the binary cross entropy between ground truth and predicted probabilities.
Used for binary classification problems where values should be in [0, 1].

Formula: -(y_true * log(y_pred) + (1 - y_true) * log(1 - y_pred))

Note: Predictions are clamped to [epsilon, 1-epsilon] for numerical stability,
so exact 0.0 and 1.0 predictions produce large but finite loss values.
"""

import math
from typing import Callable

# Default epsilon for numerical stability
DEFAULT_BCE_EPSILON = 1e-15


def make_bce_loss(epsilon: float = DEFAULT_BCE_EPSILON) -> Callable[[float, float], float]:
    """
    Create a binary cross entropy loss function with a custom epsilon.

    Args:
        epsilon: Small value for numerical stability to avoid log(0).
                 Default is 1e-15.

    Returns:
        A loss function with signature (ground_truth, predicted) -> float

    Examples:
        >>> bce = make_bce_loss(epsilon=1e-15)
        >>> round(bce(1.0, 0.9), 6)
        0.105361
    """

    def _bce_loss(ground_truth: float, predicted: float) -> float:
        if ground_truth < 0 or ground_truth > 1:
            raise ValueError(f"ground_truth must be in [0, 1], got {ground_truth}")
        if predicted < 0 or predicted > 1:
            raise ValueError(f"predicted must be in [0, 1], got {predicted}")

        y_pred_clipped = max(min(predicted, 1.0 - epsilon), epsilon)

        loss = -(
            ground_truth * math.log(y_pred_clipped)
            + (1.0 - ground_truth) * math.log(1.0 - y_pred_clipped)
        )

        return max(loss, 0.0)

    return _bce_loss


# Default BCE loss function
binary_cross_entropy_loss = make_bce_loss(DEFAULT_BCE_EPSILON)
binary_cross_entropy_loss.__doc__ = """
Calculate binary cross entropy loss with the default epsilon=1e-15.

Args:
    ground_truth: The true label (must be in [0, 1])
    predicted: The predicted probability (must be in [0, 1])

Returns:
    The binary cross entropy loss

Raises:
    ValueError: If ground_truth or predicted is outside [0, 1]

Examples:
    >>> round(binary_cross_entropy_loss(1.0, 0.7), 6)
    0.356675
    >>> round(binary_cross_entropy_loss(0.0, 0.3), 6)
    0.356675
    >>> round(binary_cross_entropy_loss(1.0, 0.9), 6)
    0.105361
"""

