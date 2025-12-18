"""
Log-Cosh Loss Function

Computes the logarithm of the hyperbolic cosine of the error.
This loss function is similar to mean squared error but is less sensitive
to outliers due to its logarithmic nature.

Formula: log(cosh(y_pred - y_true))

Note: For large errors, this approximates absolute error minus log(2).
      For small errors, this approximates squared error / 2.
"""

import math


def log_cosh_loss(ground_truth: float, predicted: float) -> float:
    """
    Calculate Log-Cosh loss.

    Args:
        ground_truth: The true/actual value
        predicted: The predicted value

    Returns:
        The Log-Cosh loss: log(cosh(predicted - ground_truth))

    Examples:
        >>> round(log_cosh_loss(0.0, 0.0), 10)
        0.0
        >>> round(log_cosh_loss(0.0, 1.0), 6)
        0.433781
    """
    error = predicted - ground_truth
    # Use math.cosh and math.log for standard float precision
    # Handle potential overflow for very large errors
    try:
        return math.log(math.cosh(error))
    except OverflowError:
        # For very large values, log(cosh(x)) ≈ |x| - log(2)
        return abs(error) - math.log(2)

