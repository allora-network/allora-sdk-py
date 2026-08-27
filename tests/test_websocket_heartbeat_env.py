"""WEBSOCKET_HEARTBEAT parsing in AlloraNetworkConfig.from_env."""

import pytest

from allora_sdk.rpc_client.config import AlloraNetworkConfig


def _env(monkeypatch, **overrides):
    base = {
        "CHAIN_ID": "allora-testnet-1",
        "RPC_ENDPOINT": "grpc+https://x:443",
        "WEBSOCKET_ENDPOINT": "wss://x/websocket",
        "FAUCET_URL": "https://x",
        "FEE_DENOM": "uallo",
        "FEE_MIN_GAS_PRICE": "10",
    }
    base.update(overrides)
    for k, v in base.items():
        monkeypatch.setenv(k, v)


@pytest.mark.parametrize("raw,expected", [
    ("false", False), ("0", False), ("no", False),
    ("true", True), ("1", True), ("YES", True),
])
def test_known_spellings(monkeypatch, raw, expected):
    _env(monkeypatch, WEBSOCKET_HEARTBEAT=raw)
    assert AlloraNetworkConfig.from_env().websocket_heartbeat is expected


def test_unset_defaults_to_enabled(monkeypatch):
    _env(monkeypatch)
    monkeypatch.delenv("WEBSOCKET_HEARTBEAT", raising=False)
    assert AlloraNetworkConfig.from_env().websocket_heartbeat is True


def test_blank_is_treated_as_unset(monkeypatch):
    """k8s renders `value: ""` for an unconfigured knob; that must not disable
    the heartbeat."""
    _env(monkeypatch, WEBSOCKET_HEARTBEAT="")
    assert AlloraNetworkConfig.from_env().websocket_heartbeat is True


@pytest.mark.parametrize("raw", ["flase", "ture", "on", "off", "2"])
def test_typo_raises_instead_of_silently_disabling(monkeypatch, raw):
    """Mapping an unrecognised value to False would disable the heartbeat fleet
    wide with no signal -- the exact failure it exists to prevent."""
    _env(monkeypatch, WEBSOCKET_HEARTBEAT=raw)
    with pytest.raises(ValueError, match="WEBSOCKET_HEARTBEAT"):
        AlloraNetworkConfig.from_env()
