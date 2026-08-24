"""
Cover the env-var surface of AlloraRPCClient.from_env: the tx settings are
configured through the environment in hosting, so a parsing bug here is
invisible to every test that builds a TxManager directly.
"""
import pytest

from allora_sdk.rpc_client.client import AlloraRPCClient, resolve_tx_settings_from_env, _env_bool, _env_number
from allora_sdk.rpc_client.config import AlloraNetworkConfig, AlloraWalletConfig


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("Yes", True), (" true ", True),
    ("0", False), ("false", False), ("FALSE", False), ("no", False),
])
def test_env_bool_accepts_known_spellings(monkeypatch, raw, expected):
    monkeypatch.setenv("SIMULATE_GAS_FROM_START", raw)
    assert _env_bool("SIMULATE_GAS_FROM_START") is expected


def test_env_bool_unset_is_none(monkeypatch):
    monkeypatch.delenv("SIMULATE_GAS_FROM_START", raising=False)
    assert _env_bool("SIMULATE_GAS_FROM_START") is None


@pytest.mark.parametrize("raw", ["flase", "tru", "on", "off", "2", ""])
def test_env_bool_rejects_unrecognised(monkeypatch, raw):
    # "flase" silently meaning False is the bug this guards: a typo would
    # otherwise disable gas simulation across the fleet with no signal.
    monkeypatch.setenv("SIMULATE_GAS_FROM_START", raw)
    if raw == "":
        # Empty is indistinguishable from unset for an optional knob.
        assert _env_bool("SIMULATE_GAS_FROM_START") is None
    else:
        with pytest.raises(ValueError, match="SIMULATE_GAS_FROM_START"):
            _env_bool("SIMULATE_GAS_FROM_START")


def test_env_number_parses(monkeypatch):
    monkeypatch.setenv("MAX_FEES", "5000")
    monkeypatch.setenv("GAS_ADJUSTMENT", "1.8")
    assert _env_number("MAX_FEES", int) == 5000
    assert _env_number("GAS_ADJUSTMENT", float) == 1.8


def test_env_number_unset_is_none(monkeypatch):
    monkeypatch.delenv("BASE_GAS", raising=False)
    assert _env_number("BASE_GAS", int) is None


def test_env_number_names_the_variable_on_garbage(monkeypatch):
    monkeypatch.setenv("MAX_FEES", "1.5x")
    with pytest.raises(ValueError, match="MAX_FEES"):
        _env_number("MAX_FEES", int)


# --- FEE_GRANTER ---

MNEMONIC = "abandon " * 11 + "about"
# Synthetic addresses: tests must not carry real fleet addresses into a public repo.
GRANTER = "allo18m98xemapflq86kh9j6v358l5n5rp2ahfaekth"
OTHER_GRANTER = "allo1n8jhl6gnk8ha7epyqeft6vgp6clgrnujkl6980"


def _fee_granter_from_env(monkeypatch, raw):
    """Run from_env far enough to capture the fee_granter it would pass along."""
    captured = {}

    def _fake_init(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(AlloraRPCClient, "__init__", _fake_init)
    if raw is None:
        monkeypatch.delenv("FEE_GRANTER", raising=False)
    else:
        monkeypatch.setenv("FEE_GRANTER", raw)
    # network and wallet are passed explicitly so from_env only exercises
    # the FEE_GRANTER branch under test.
    AlloraRPCClient.from_env(
        network=AlloraNetworkConfig.testnet(),
        wallet=AlloraWalletConfig(mnemonic=MNEMONIC),
    )
    return captured["fee_granter"]


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_blank_fee_granter_is_treated_as_unset(monkeypatch, raw):
    # A templated k8s value that renders empty must not reach bech32
    # validation, which would reject it and block startup entirely.
    assert _fee_granter_from_env(monkeypatch, raw) is None


def test_fee_granter_is_forwarded_when_set(monkeypatch):
    granter = "allo18m98xemapflq86kh9j6v358l5n5rp2ahfaekth"
    assert _fee_granter_from_env(monkeypatch, granter) == granter


def test_resolve_tx_settings_reads_env_for_unset_values(monkeypatch):
    monkeypatch.setenv("FEE_GRANTER", GRANTER)
    monkeypatch.setenv("GAS_ADJUSTMENT", "1.4")
    monkeypatch.setenv("BASE_GAS", "")
    settings = resolve_tx_settings_from_env()
    assert settings["fee_granter"] == GRANTER
    assert settings["gas_adjustment"] == 1.4
    assert settings["base_gas"] is None


def test_resolve_tx_settings_does_not_override_explicit_values(monkeypatch):
    monkeypatch.setenv("FEE_GRANTER", GRANTER)
    assert resolve_tx_settings_from_env(fee_granter=OTHER_GRANTER)["fee_granter"] == OTHER_GRANTER


def test_network_config_from_env_treats_blank_as_unset(monkeypatch):
    """A k8s manifest renders `value: ""` for unconfigured knobs."""
    for name, value in [
        ("CHAIN_ID", "allora-testnet-1"),
        ("RPC_ENDPOINT", "grpc+http://localhost:9090"),
        ("WEBSOCKET_ENDPOINT", "ws://localhost:26657/websocket"),
        ("FAUCET_URL", "http://localhost:8000"),
        ("FEE_DENOM", "uallo"),
        ("FEE_MIN_GAS_PRICE", "10"),
        ("EVENT_RECV_TIMEOUT_SECS", "   "),
    ]:
        monkeypatch.setenv(name, value)
    assert AlloraNetworkConfig.from_env().event_recv_timeout_secs == 30.0

    monkeypatch.setenv("EVENT_RECV_TIMEOUT_SECS", "not-a-number")
    with pytest.raises(RuntimeError, match="EVENT_RECV_TIMEOUT_SECS"):
        AlloraNetworkConfig.from_env()
