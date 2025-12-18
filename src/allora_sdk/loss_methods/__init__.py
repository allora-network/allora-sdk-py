"""
Allora SDK Default Loss Methods

This subpackage provides default implementations of common loss functions
used on the Allora Network. When a reputer is created without an explicit
loss function, the SDK will automatically select the appropriate default
based on the topic's on-chain `loss_method` configuration.

Supported loss methods:
- sqe (squared error) - aliases: mse
- abse (absolute error) - aliases: mae
- huber (Huber loss)
- logcosh (Log-Cosh loss)
- bce (binary cross entropy)
- poisson (Poisson loss)
- ztae (Z-Transformed Absolute Error) - requires std parameter
- zptae (Z Power-Tanh Absolute Error) - requires std parameter

Example usage:
    from allora_sdk.loss_methods import get_default_loss_fn, squared_error_loss

    # Get a loss function by name (useful for custom topic configs)
    loss_fn = get_default_loss_fn("sqe")

    # Or use one directly
    loss = squared_error_loss(ground_truth=100.0, predicted=95.0)

    # For ztae/zptae, create custom functions with appropriate std:
    from allora_sdk.loss_methods import make_ztae_loss, make_zptae_loss
    ztae = make_ztae_loss(std=0.02)
    zptae = make_zptae_loss(std=0.02, alpha=0.25, beta=2.0)
"""

from .defaults import (
    get_default_loss_fn,
    is_supported_loss_method,
    SUPPORTED_LOSS_METHODS,
    UnsupportedLossMethodError,
)
from .squared_error import squared_error_loss
from .absolute_error import absolute_error_loss
from .huber import huber_loss, make_huber_loss
from .log_cosh import log_cosh_loss
from .binary_cross_entropy import binary_cross_entropy_loss, make_bce_loss
from .poisson import poisson_loss, make_poisson_loss
from .ztae import ztae_loss, make_ztae_loss
from .zptae import zptae_loss, make_zptae_loss

__all__ = [
    # Main API
    "get_default_loss_fn",
    "is_supported_loss_method",
    "SUPPORTED_LOSS_METHODS",
    "UnsupportedLossMethodError",
    # Individual loss functions
    "squared_error_loss",
    "absolute_error_loss",
    "huber_loss",
    "make_huber_loss",
    "log_cosh_loss",
    "binary_cross_entropy_loss",
    "make_bce_loss",
    "poisson_loss",
    "make_poisson_loss",
    "ztae_loss",
    "make_ztae_loss",
    "zptae_loss",
    "make_zptae_loss",
]

