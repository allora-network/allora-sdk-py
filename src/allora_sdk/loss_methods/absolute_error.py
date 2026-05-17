"""
Absolute Error Loss Function (abse)

Computes the absolute difference between ground truth and predicted values.
Also known as Mean Absolute Error (MAE) when averaged over multiple samples.

Formula: |y_true - y_pred|
"""


def absolute_error_loss(ground_truth: float, predicted: float) -> float:
    """
    Calculate absolute error loss.

    Args:
        ground_truth: The true/actual value
        predicted: The predicted value

    Returns:
        The absolute error: |ground_truth - predicted|

    Examples:
        >>> absolute_error_loss(100.0, 95.0)
        5.0
        >>> absolute_error_loss(3.0, 5.0)
        2.0
        >>> absolute_error_loss(-1.0, 1.0)
        2.0
    """
    return abs(ground_truth - predicted)

