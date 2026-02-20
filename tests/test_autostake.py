from __future__ import annotations

from unittest.mock import Mock

import pytest

from allora_sdk.worker.autostake import (
    AutoStakeConfig,
    AutoStakeTargetType,
    extract_reward_amount_uallo,
    normalize_actor_type,
)


# ---------------------------------------------------------------------------
# extract_reward_amount_uallo
# ---------------------------------------------------------------------------


def _make_event(addresses: list[str], rewards: list[str]) -> Mock:
    event = Mock()
    event.addresses = addresses
    event.rewards = rewards
    return event


class TestExtractRewardAmountUallo:
    def test_wallet_found_returns_reward(self):
        event = _make_event(["allo1abc", "allo1def"], ["1000", "2000"])
        assert extract_reward_amount_uallo(event, "allo1def") == 2000

    def test_first_wallet_returns_first_reward(self):
        event = _make_event(["allo1abc", "allo1def"], ["500", "700"])
        assert extract_reward_amount_uallo(event, "allo1abc") == 500

    def test_wallet_not_in_addresses_returns_none(self):
        event = _make_event(["allo1abc"], ["1000"])
        assert extract_reward_amount_uallo(event, "allo1missing") is None

    def test_index_out_of_bounds_returns_none(self):
        event = _make_event(["allo1abc", "allo1def"], ["1000"])
        assert extract_reward_amount_uallo(event, "allo1def") is None

    def test_decimal_reward_truncated(self):
        event = _make_event(["allo1abc"], ["12345.6789"])
        assert extract_reward_amount_uallo(event, "allo1abc") == 12345

    def test_large_decimal_reward(self):
        event = _make_event(["allo1abc"], ["999999999999999999.999"])
        assert extract_reward_amount_uallo(event, "allo1abc") == 999999999999999999

    def test_non_numeric_reward_returns_none(self):
        event = _make_event(["allo1abc"], ["not_a_number"])
        assert extract_reward_amount_uallo(event, "allo1abc") is None

    def test_empty_addresses(self):
        event = _make_event([], [])
        assert extract_reward_amount_uallo(event, "allo1abc") is None


# ---------------------------------------------------------------------------
# normalize_actor_type
# ---------------------------------------------------------------------------


class TestNormalizeActorType:
    def test_plain_string(self):
        assert normalize_actor_type("ACTOR_TYPE_FORECASTER") == "ACTOR_TYPE_FORECASTER"

    def test_quoted_string(self):
        assert normalize_actor_type('"ACTOR_TYPE_FORECASTER"') == "ACTOR_TYPE_FORECASTER"

    def test_prefixed_enum_repr(self):
        assert normalize_actor_type("ActorType.FORECASTER") == "ACTOR_TYPE_FORECASTER"

    def test_object_with_name_attribute(self):
        obj = Mock()
        obj.name = "ACTOR_TYPE_FORECASTER"
        assert normalize_actor_type(obj) == "ACTOR_TYPE_FORECASTER"

    def test_bare_name_gets_prefix(self):
        assert normalize_actor_type("INFERER_UNSPECIFIED") == "ACTOR_TYPE_INFERER_UNSPECIFIED"

    def test_bare_forecaster(self):
        assert normalize_actor_type("FORECASTER") == "ACTOR_TYPE_FORECASTER"


# ---------------------------------------------------------------------------
# AutoStakeConfig
# ---------------------------------------------------------------------------


class TestAutoStakeConfig:
    def test_string_target_type_converted(self):
        cfg = AutoStakeConfig(target_type="reputer", target_address="allo1abc")  # type: ignore[arg-type]
        assert cfg.target_type is AutoStakeTargetType.REPUTER

    def test_enum_target_type_preserved(self):
        cfg = AutoStakeConfig(target_type=AutoStakeTargetType.VALIDATOR, target_address="allovaloper1abc")
        assert cfg.target_type is AutoStakeTargetType.VALIDATOR

    def test_invalid_string_target_type_raises(self):
        with pytest.raises(ValueError):
            AutoStakeConfig(target_type="bogus", target_address="allo1abc")  # type: ignore[arg-type]
