"""AlloraNetworkConfig.gas_adjustment validation (see PR #84 cubic review)."""

import logging

import pytest

from allora_sdk.rpc_client.config import AlloraNetworkConfig


def _cfg(gas_adjustment: float) -> AlloraNetworkConfig:
    return AlloraNetworkConfig(
        chain_id="allora-testnet-1",
        url="grpc+https://example:443",
        gas_adjustment=gas_adjustment,
    )


@pytest.mark.parametrize("bad", [0, 0.0, -0.5, -1])
def test_non_positive_gas_adjustment_raises(bad):
    # A zero/negative multiplier (bad caller value or GAS_ADJUSTMENT typo) would
    # guarantee out-of-gas — must fail loudly at construction, not silently.
    with pytest.raises(ValueError, match="gas_adjustment must be > 0"):
        _cfg(bad)


def test_gas_adjustment_below_one_warns_but_allowed(caplog):
    with caplog.at_level(logging.WARNING, logger="allora_sdk"):
        cfg = _cfg(0.5)
    assert cfg.gas_adjustment == 0.5
    assert any("below 1.0" in r.message for r in caplog.records)


@pytest.mark.parametrize("good", [1.0, 1.4, 2.0])
def test_valid_gas_adjustment_accepted_silently(good, caplog):
    with caplog.at_level(logging.WARNING, logger="allora_sdk"):
        cfg = _cfg(good)
    assert cfg.gas_adjustment == good
    assert not any("gas_adjustment" in r.message for r in caplog.records)


def test_factories_default_to_one():
    assert AlloraNetworkConfig.testnet().gas_adjustment == 1.0
    assert AlloraNetworkConfig.mainnet().gas_adjustment == 1.0
