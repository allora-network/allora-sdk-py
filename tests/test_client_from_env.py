"""
Cover the env-var surface of AlloraRPCClient.from_env: the tx settings are
configured through the environment in hosting, so a parsing bug here is
invisible to every test that builds a TxManager directly.
"""
import pytest

from allora_sdk.rpc_client.client import _env_bool, _env_number


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
