"""Tests for reputer loss computation safety (non-finite handling, ztae/zptae config)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from allora_sdk.loss_methods.defaults import requires_explicit_loss_fn
from allora_sdk.loss_methods.squared_error import squared_error_loss
from allora_sdk.worker.reputer import Reputer


# ---------------------------------------------------------------------------
# Non-finite input handling in _compute_loss_bundle
# ---------------------------------------------------------------------------


def _make_value_bundle(combined_value: str, naive_value: str = "0") -> Mock:
    vb = Mock()
    vb.combined_value = combined_value
    vb.naive_value = naive_value
    vb.inferer_values = []
    vb.forecaster_values = []
    vb.one_out_inferer_values = []
    vb.one_out_forecaster_values = []
    vb.one_in_forecaster_values = []
    vb.one_out_inferer_forecaster_values = []
    return vb


@pytest.fixture
def reputer_with_sqe():
    wallet = Mock()
    wallet.address.return_value = Mock(__str__=lambda: "allo1abc")
    client = Mock()

    async def reputer_fn(_ctx, prediction: float) -> float:
        return squared_error_loss(10.0, prediction)

    return Reputer(
        wallet=wallet,
        client=client,
        topic_id=1,
        reputer_fn=reputer_fn,
    )


class TestComputeLossBundleNonFinite:
    """Test that _compute_loss_bundle rejects non-finite predicted values."""

    @pytest.mark.asyncio
    async def test_predicted_nan_in_combined_raises(self, reputer_with_sqe):
        vb = _make_value_bundle(combined_value="nan", naive_value="0")
        with pytest.raises(ValueError) as exc_info:
            await reputer_with_sqe._compute_loss_bundle(vb)
        assert "predicted" in str(exc_info.value)
        assert "finite" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_predicted_inf_in_combined_raises(self, reputer_with_sqe):
        vb = _make_value_bundle(combined_value="inf", naive_value="0")
        with pytest.raises(ValueError) as exc_info:
            await reputer_with_sqe._compute_loss_bundle(vb)
        assert "predicted" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_finite_values_succeed(self, reputer_with_sqe):
        vb = _make_value_bundle(combined_value="8.0", naive_value="9.0")
        result = await reputer_with_sqe._compute_loss_bundle(vb)
        assert result.combined_value == "4.0"  # (10-8)^2 = 4
        assert result.naive_value == "1.0"  # (10-9)^2 = 1


# ---------------------------------------------------------------------------
# ZTAE/ZPTAE explicit configuration requirement
# ---------------------------------------------------------------------------


class TestRequiresExplicitLossFn:
    """Test requires_explicit_loss_fn helper."""

    @pytest.mark.parametrize("method", ["ztae", "zptae", "ZTAE", "ztae_loss", "zptae_loss"])
    def test_ztae_zptae_require_explicit(self, method: str):
        assert requires_explicit_loss_fn(method) is True

    @pytest.mark.parametrize("method", ["sqe", "abse", "huber", "bce", "poisson"])
    def test_other_methods_no_requirement(self, method: str):
        assert requires_explicit_loss_fn(method) is False

