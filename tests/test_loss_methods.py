"""Tests for loss functions in allora_sdk.loss_methods."""

import pytest

from allora_sdk.loss_methods.squared_error import squared_error_loss
from allora_sdk.loss_methods.absolute_error import absolute_error_loss
from allora_sdk.loss_methods.huber import huber_loss, make_huber_loss
from allora_sdk.loss_methods.log_cosh import log_cosh_loss
from allora_sdk.loss_methods.binary_cross_entropy import (
    binary_cross_entropy_loss,
    make_bce_loss,
)
from allora_sdk.loss_methods.poisson import poisson_loss, make_poisson_loss
from allora_sdk.loss_methods.ztae import ztae_loss, make_ztae_loss
from allora_sdk.loss_methods.zptae import zptae_loss, make_zptae_loss
from allora_sdk.loss_methods.defaults import (
    get_default_loss_fn,
    is_supported_loss_method,
    UnsupportedLossMethodError,
)


# ── Squared Error ──────────────────────────────────────────────────────────


def test_squared_error_basic():
    assert squared_error_loss(100.0, 95.0) == 25.0
    assert squared_error_loss(3.0, 5.0) == 4.0


def test_squared_error_zero_error():
    assert squared_error_loss(42.0, 42.0) == 0.0


def test_squared_error_large_values():
    # (1e6 + 1000 - 1e6)^2 = 1000^2 = 1_000_000
    assert squared_error_loss(1e6, 1e6 + 1000) == 1_000_000.0


# ── Absolute Error ─────────────────────────────────────────────────────────


def test_absolute_error_basic():
    assert absolute_error_loss(100.0, 95.0) == 5.0
    assert absolute_error_loss(3.0, 5.0) == 2.0


def test_absolute_error_zero_error():
    assert absolute_error_loss(7.5, 7.5) == 0.0


def test_absolute_error_negative_values():
    assert absolute_error_loss(-1.0, 1.0) == 2.0


# ── Huber Loss ─────────────────────────────────────────────────────────────


def test_huber_quadratic_region():
    # |error| = 0.5 <= delta=1.0 → 0.5 * 0.5^2 = 0.125
    assert huber_loss(0.0, 0.5) == pytest.approx(0.125)
    # |error| = 1.0 = delta → 0.5 * 1.0^2 = 0.5
    assert huber_loss(0.0, 1.0) == pytest.approx(0.5)


def test_huber_linear_region():
    # |error| = 2.0 > delta=1.0 → 1.0 * (2.0 - 0.5) = 1.5
    assert huber_loss(0.0, 2.0) == pytest.approx(1.5)


def test_huber_zero_error():
    assert huber_loss(5.0, 5.0) == 0.0


def test_make_huber_loss_custom_delta():
    huber2 = make_huber_loss(delta=2.0)
    # |error| = 1.0 <= delta=2.0 → 0.5 * 1.0^2 = 0.5
    assert huber2(0.0, 1.0) == pytest.approx(0.5)
    # |error| = 3.0 > delta=2.0 → 2.0 * (3.0 - 1.0) = 4.0
    assert huber2(0.0, 3.0) == pytest.approx(4.0)


def test_make_huber_loss_invalid_delta():
    with pytest.raises(ValueError):
        make_huber_loss(delta=0)
    with pytest.raises(ValueError):
        make_huber_loss(delta=-1)


# ── Log-Cosh Loss ──────────────────────────────────────────────────────────


def test_log_cosh_zero_error():
    assert log_cosh_loss(5.0, 5.0) == pytest.approx(0.0, abs=1e-10)


def test_log_cosh_known_value():
    # log(cosh(1)) ≈ 0.43378
    assert log_cosh_loss(0.0, 1.0) == pytest.approx(0.433781, rel=1e-4)


def test_log_cosh_symmetry():
    assert log_cosh_loss(0.0, 2.0) == pytest.approx(log_cosh_loss(2.0, 0.0))


def test_log_cosh_overflow_handling():
    # For very large x: log(cosh(x)) ≈ |x| - ln(2) ≈ |x| - 0.693147
    result = log_cosh_loss(0.0, 1000.0)
    assert result == pytest.approx(1000.0 - 0.6931471805599453, rel=1e-6)


# ── Binary Cross Entropy ──────────────────────────────────────────────────


def test_bce_confident_correct():
    # -(1·log(0.9) + 0·log(0.1)) = -log(0.9) ≈ 0.10536
    assert binary_cross_entropy_loss(1.0, 0.9) == pytest.approx(0.105361, rel=1e-4)


def test_bce_symmetric_case():
    # BCE(1, 0.7) = -log(0.7) ≈ 0.35667
    # BCE(0, 0.3) = -log(0.7) ≈ 0.35667
    assert binary_cross_entropy_loss(1.0, 0.7) == pytest.approx(
        binary_cross_entropy_loss(0.0, 0.3)
    )


def test_bce_boundary_prediction_finite():
    result = binary_cross_entropy_loss(1.0, 0.0)
    assert result > 30.0
    assert result < float("inf")


@pytest.mark.parametrize(
    "gt,pred",
    [
        (1.5, 0.5),
        (-0.1, 0.5),
        (0.5, 1.1),
        (0.5, -0.1),
    ],
)
def test_bce_invalid_inputs(gt: float, pred: float):
    with pytest.raises(ValueError):
        binary_cross_entropy_loss(gt, pred)


def test_make_bce_loss_custom_epsilon():
    bce_coarse = make_bce_loss(epsilon=0.01)
    # Larger epsilon clamps harder → smaller loss at boundary
    assert bce_coarse(1.0, 0.0) < binary_cross_entropy_loss(1.0, 0.0)


# ── Poisson Loss ───────────────────────────────────────────────────────────


def test_poisson_basic():
    # pred - gt·log(pred) = 1 - 0·log(1) = 1.0
    assert poisson_loss(0.0, 1.0) == pytest.approx(1.0)


def test_poisson_known_value():
    # 3 - 2·ln(3) = 3 - 2·1.09861… ≈ 0.80278
    assert poisson_loss(2.0, 3.0) == pytest.approx(0.80278, rel=1e-3)


def test_poisson_zero_predicted_uses_epsilon():
    result = poisson_loss(1.0, 0.0)
    assert result > 0
    assert result < float("inf")


@pytest.mark.parametrize(
    "gt,pred",
    [(-1.0, 1.0), (1.0, -1.0)],
)
def test_poisson_negative_raises(gt: float, pred: float):
    with pytest.raises(ValueError):
        poisson_loss(gt, pred)


def test_make_poisson_loss_custom_epsilon():
    custom = make_poisson_loss(epsilon=0.1)
    # pred=0 → clamped to 0.1; loss = 0.1 - 0·log(0.1) = 0.1
    assert custom(0.0, 0.0) == pytest.approx(0.1)


# ── ZTAE ───────────────────────────────────────────────────────────────────


def test_ztae_zero_error():
    assert ztae_loss(3.0, 3.0) == 0.0


def test_ztae_known_value():
    # std=1, mean=0: |tanh(1) - tanh(0)| ≈ |0.76159 - 0| ≈ 0.76159
    assert ztae_loss(1.0, 0.0) == pytest.approx(0.76159, rel=1e-3)


def test_ztae_bounded_output():
    # tanh ∈ [-1, 1] → |tanh(a) - tanh(b)| ≤ 2
    assert ztae_loss(1e10, -1e10) <= 2.0


def test_make_ztae_loss_custom_params():
    ztae = make_ztae_loss(std=0.5, mean=1.0)
    assert ztae(1.0, 1.0) == 0.0
    assert ztae(1.5, 1.0) > 0


def test_make_ztae_loss_invalid_std():
    with pytest.raises(ValueError):
        make_ztae_loss(std=0)
    with pytest.raises(ValueError):
        make_ztae_loss(std=-1)


# ── ZPTAE ──────────────────────────────────────────────────────────────────


def test_zptae_zero_error():
    assert zptae_loss(0.0, 0.0) == 0.0


def test_zptae_positive_for_nonzero_error():
    assert zptae_loss(1.0, 0.0) > 0


def test_make_zptae_loss_custom_params():
    fn = make_zptae_loss(std=2.0, mean=1.0, alpha=0.5, beta=1.0, mse_norm=0.1, gamma=2.0)
    assert fn(1.0, 1.0) == 0.0
    assert fn(3.0, 1.0) > 0


def test_make_zptae_loss_invalid_std():
    with pytest.raises(ValueError):
        make_zptae_loss(std=0)
    with pytest.raises(ValueError):
        make_zptae_loss(std=-1)


# ── Defaults registry ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["sqe", "abse", "huber", "logcosh", "bce", "poisson", "ztae", "zptae"],
)
def test_get_default_loss_fn_canonical(name: str):
    fn = get_default_loss_fn(name)
    assert callable(fn)


@pytest.mark.parametrize(
    "alias,canonical,gt,pred",
    [
        ("mse", "sqe", 10.0, 8.0),
        ("mae", "abse", 10.0, 8.0),
        ("squared_error", "sqe", 10.0, 8.0),
        ("absolute_error", "abse", 10.0, 8.0),
        ("log_cosh", "logcosh", 10.0, 8.0),
        ("binary_cross_entropy", "bce", 0.8, 0.6),
        ("poisson_loss", "poisson", 2.0, 3.0),
    ],
)
def test_get_default_loss_fn_alias_matches_canonical(
    alias: str, canonical: str, gt: float, pred: float
):
    assert get_default_loss_fn(alias)(gt, pred) == get_default_loss_fn(canonical)(gt, pred)


def test_get_default_loss_fn_case_insensitive():
    fn = get_default_loss_fn("SQE")
    assert fn(4.0, 2.0) == 4.0


def test_get_default_loss_fn_unsupported():
    with pytest.raises(UnsupportedLossMethodError) as exc_info:
        get_default_loss_fn("nonexistent")
    assert "nonexistent" in str(exc_info.value)


@pytest.mark.parametrize(
    "name",
    ["sqe", "mse", "abse", "mae", "huber", "logcosh", "bce", "poisson", "ztae", "zptae"],
)
def test_is_supported_loss_method_true(name: str):
    assert is_supported_loss_method(name) is True


def test_is_supported_loss_method_false():
    assert is_supported_loss_method("made_up_loss") is False


def test_unsupported_loss_method_error_attributes():
    err = UnsupportedLossMethodError("fake", ["sqe", "abse"])
    assert err.loss_method == "fake"
    assert err.supported == ["sqe", "abse"]
    assert "fake" in str(err)
