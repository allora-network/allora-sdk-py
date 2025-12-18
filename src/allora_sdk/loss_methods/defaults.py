from typing import Callable, Optional

from .squared_error import squared_error_loss
from .absolute_error import absolute_error_loss
from .huber import huber_loss
from .log_cosh import log_cosh_loss
from .binary_cross_entropy import binary_cross_entropy_loss
from .poisson import poisson_loss
from .ztae import ztae_loss
from .zptae import zptae_loss

LossFn = Callable[[float, float], float]

class UnsupportedLossMethodError(Exception):
    """Raised when a loss_method is not supported by the SDK's default implementations."""

    def __init__(self, loss_method: str, supported: list[str]):
        self.loss_method = loss_method
        self.supported = supported
        super().__init__(
            f"Unsupported loss method: '{loss_method}'. "
            f"Supported methods: {sorted(supported)}. "
            f"Please provide a custom loss_fn to AlloraWorker.reputer()."
        )


# Primary mapping: canonical loss_method names to functions
_LOSS_FUNCTIONS: dict[str, LossFn] = {
    "sqe": squared_error_loss,
    "abse": absolute_error_loss,
    "huber": huber_loss,
    "logcosh": log_cosh_loss,
    "bce": binary_cross_entropy_loss,
    "poisson": poisson_loss,
    "ztae": ztae_loss,
    "zptae": zptae_loss,
}

# Alias mapping: alternative names -> canonical names
_ALIASES: dict[str, str] = {
    # Squared error aliases
    "mse": "sqe",
    "squared_error": "sqe",
    "se": "sqe",
    # Absolute error aliases
    "mae": "abse",
    "absolute_error": "abse",
    "ae": "abse",
    # Huber aliases
    "huber_loss": "huber",
    # Log-cosh aliases
    "log_cosh": "logcosh",
    "logcosh_loss": "logcosh",
    # BCE aliases
    "binary_cross_entropy": "bce",
    "bce_loss": "bce",
    # Poisson aliases
    "poisson_loss": "poisson",
    # ZTAE aliases
    "ztae_loss": "ztae",
    "z_transformed_absolute_error": "ztae",
    # ZPTAE aliases
    "zptae_loss": "zptae",
    "z_power_tanh_absolute_error": "zptae",
}

# Set of all supported method names (canonical + aliases)
SUPPORTED_LOSS_METHODS: frozenset[str] = frozenset(
    list(_LOSS_FUNCTIONS.keys()) + list(_ALIASES.keys())
)

def _normalize_loss_method(loss_method: str) -> str:
    normalized = loss_method.lower().strip()
    if normalized in _ALIASES:
        return _ALIASES[normalized]
    return normalized


def is_supported_loss_method(loss_method: str) -> bool:
    normalized = _normalize_loss_method(loss_method)
    return normalized in _LOSS_FUNCTIONS


def get_default_loss_fn(loss_method: str) -> LossFn:
    """
    Get the default loss function for a given loss method name.

    This function supports the canonical names used on-chain as well as
    common aliases for convenience.

    Args:
        loss_method: The loss method name (case-insensitive).
                     Supported values:
                     - "sqe" (or "mse", "squared_error")
                     - "abse" (or "mae", "absolute_error")
                     - "huber"
                     - "logcosh" (or "log_cosh")
                     - "bce" (or "binary_cross_entropy")
                     - "poisson"
                     - "ztae" (Z-Transformed Absolute Error)
                     - "zptae" (Z Power-Tanh Absolute Error)

    Returns:
        A callable with signature (ground_truth: float, predicted: float) -> float

    Raises:
        UnsupportedLossMethodError: If the loss method is not supported

    Note:
        For ztae and zptae, the default functions use std=1.0. For proper use
        with real data, create custom functions using make_ztae_loss() or
        make_zptae_loss() with the appropriate standard deviation.

    Examples:
        >>> loss_fn = get_default_loss_fn("sqe")
        >>> loss_fn(100.0, 95.0)
        25.0

        >>> loss_fn = get_default_loss_fn("mae")  # alias for abse
        >>> loss_fn(100.0, 95.0)
        5.0
    """
    normalized = _normalize_loss_method(loss_method)

    if normalized not in _LOSS_FUNCTIONS:
        raise UnsupportedLossMethodError(
            loss_method=loss_method,
            supported=list(_LOSS_FUNCTIONS.keys()),
        )

    return _LOSS_FUNCTIONS[normalized]

