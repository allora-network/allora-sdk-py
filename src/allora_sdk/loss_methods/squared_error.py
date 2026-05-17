"""
Squared Error Loss Function (sqe)

Computes the squared difference between ground truth and predicted values.
This is the default loss function used by the SDK when no specific method is configured.

Formula: (y_true - y_pred)^2
"""


def squared_error_loss(ground_truth: float, predicted: float) -> float:
    """
    Calculate squared error loss.

    Args:
        ground_truth: The true/actual value
        predicted: The predicted value

    Returns:
        The squared error: (ground_truth - predicted)^2

    Examples:
        >>> squared_error_loss(100.0, 95.0)
        25.0
        >>> squared_error_loss(3.0, 5.0)
        4.0
    """
    return (ground_truth - predicted) ** 2

