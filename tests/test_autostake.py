from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from allora_sdk.worker.autostake import (
    AutoStakeConfig,
    AutoStakeRole,
    AutoStakeTargetType,
    extract_reward_amount_uallo,
    make_autostake_key,
    normalize_actor_type,
    process_autostake_rewards_settled,
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


# ---------------------------------------------------------------------------
# make_autostake_key (idempotence key construction)
# ---------------------------------------------------------------------------


class TestMakeAutostakeKey:
    def test_deterministic_for_same_inputs(self):
        key1 = make_autostake_key(
            role=AutoStakeRole.INFERER,
            topic_id=42,
            target_type=AutoStakeTargetType.REPUTER,
            target_address="allo1reputer",
            block_height=1000,
            block_height_tx=1005,
            reward_uallo=500,
        )
        key2 = make_autostake_key(
            role=AutoStakeRole.INFERER,
            topic_id=42,
            target_type=AutoStakeTargetType.REPUTER,
            target_address="allo1reputer",
            block_height=1000,
            block_height_tx=1005,
            reward_uallo=500,
        )
        assert key1 == key2

    def test_includes_all_fields(self):
        key = make_autostake_key(
            role=AutoStakeRole.FORECASTER,
            topic_id=69,
            target_type=AutoStakeTargetType.VALIDATOR,
            target_address="allovaloper1xyz",
            block_height=2000,
            block_height_tx=2010,
            reward_uallo=1000,
        )
        assert key == (
            "forecaster",
            69,
            "validator",
            "allovaloper1xyz",
            2010,
            2000,
            1000,
        )

    def test_block_height_tx_none_becomes_zero(self):
        key = make_autostake_key(
            role=AutoStakeRole.INFERER,
            topic_id=1,
            target_type=AutoStakeTargetType.REPUTER,
            target_address="allo1addr",
            block_height=100,
            block_height_tx=None,
            reward_uallo=200,
        )
        assert key[4] == 0

    def test_different_role_produces_different_key(self):
        base = dict(
            topic_id=1,
            target_type=AutoStakeTargetType.REPUTER,
            target_address="allo1addr",
            block_height=100,
            block_height_tx=105,
            reward_uallo=500,
        )
        k1 = make_autostake_key(role=AutoStakeRole.INFERER, **base)
        k2 = make_autostake_key(role=AutoStakeRole.FORECASTER, **base)
        assert k1 != k2

    def test_different_topic_produces_different_key(self):
        base = dict(
            role=AutoStakeRole.INFERER,
            target_type=AutoStakeTargetType.REPUTER,
            target_address="allo1addr",
            block_height=100,
            block_height_tx=105,
            reward_uallo=500,
        )
        k1 = make_autostake_key(topic_id=1, **base)
        k2 = make_autostake_key(topic_id=2, **base)
        assert k1 != k2

    def test_different_block_height_tx_produces_different_key(self):
        base = dict(
            role=AutoStakeRole.INFERER,
            topic_id=1,
            target_type=AutoStakeTargetType.REPUTER,
            target_address="allo1addr",
            block_height=100,
            reward_uallo=500,
        )
        k1 = make_autostake_key(block_height_tx=105, **base)
        k2 = make_autostake_key(block_height_tx=110, **base)
        assert k1 != k2


# ---------------------------------------------------------------------------
# process_autostake_rewards_settled - dedupe / replay
# ---------------------------------------------------------------------------


def _make_rewards_settled_event(
    *,
    topic_id: int = 42,
    block_height: int = 1000,
    block_height_tx: int | None = 1005,
    actor_type: str = "ACTOR_TYPE_INFERER_UNSPECIFIED",
    addresses: list[str] | None = None,
    rewards: list[str] | None = None,
) -> Mock:
    event = Mock()
    event.topic_id = topic_id
    event.block_height = block_height
    event.block_height_tx = block_height_tx
    event.actor_type = actor_type
    event.addresses = addresses or ["allo1wallet"]
    event.rewards = rewards or ["500"]
    return event


def _make_mock_client_delegate_success() -> Mock:
    client = Mock()
    pending = AsyncMock()
    resp = Mock()
    resp.code = 0
    resp.raw_log = ""
    resp.txhash = "abc123"
    pending.wait = AsyncMock(return_value=resp)
    client.emissions.tx.delegate_stake = AsyncMock(return_value=pending)
    client.network = Mock()
    client.network.fee_denom = "uallo"
    client.staking = Mock()
    client.staking.tx = Mock()
    client.staking.tx.delegate = AsyncMock(return_value=pending)
    return client


class TestProcessAutostakeRewardsSettledDedupe:
    """Tests for idempotence / dedupe behavior."""

    @pytest.mark.asyncio
    async def test_replay_same_event_skipped_when_last_key_matches(self):
        event = _make_rewards_settled_event(topic_id=42, block_height=1000, rewards=["500"])
        wallet = "allo1wallet"
        client = _make_mock_client_delegate_success()
        autostake = AutoStakeConfig(
            target_type=AutoStakeTargetType.REPUTER,
            target_address="allo1reputer",
        )
        event.addresses = [wallet]
        event.rewards = ["500"]

        # First call: processes and returns key
        result1 = await process_autostake_rewards_settled(
            role=AutoStakeRole.INFERER,
            event=event,
            topic_id=42,
            wallet_addr=wallet,
            client=client,
            autostake=autostake,
            default_fee_tier=Mock(value="PRIORITY"),
            last_autostake_key=None,
        )
        assert result1 is not None
        delegate_calls = client.emissions.tx.delegate_stake.call_count

        # Replay: same event with last_key = result1 → skipped
        result2 = await process_autostake_rewards_settled(
            role=AutoStakeRole.INFERER,
            event=event,
            topic_id=42,
            wallet_addr=wallet,
            client=client,
            autostake=autostake,
            default_fee_tier=Mock(value="PRIORITY"),
            last_autostake_key=result1,
        )
        assert result2 is None
        assert client.emissions.tx.delegate_stake.call_count == delegate_calls  # no extra call

    @pytest.mark.asyncio
    async def test_backward_compat_legacy_2tuple_matches_and_skips(self):
        """Legacy (block_height, reward_uallo) key still triggers dedupe."""
        event = _make_rewards_settled_event(topic_id=42, block_height=2000)
        event.rewards = ["300"]
        wallet = "allo1wallet"
        client = _make_mock_client_delegate_success()
        autostake = AutoStakeConfig(
            target_type=AutoStakeTargetType.REPUTER,
            target_address="allo1reputer",
        )

        # Pass legacy 2-tuple that matches (block_height=2000, reward=300)
        result = await process_autostake_rewards_settled(
            role=AutoStakeRole.INFERER,
            event=event,
            topic_id=42,
            wallet_addr=wallet,
            client=client,
            autostake=autostake,
            default_fee_tier=Mock(value="PRIORITY"),
            last_autostake_key=(2000, 300),
        )
        assert result is None
        client.emissions.tx.delegate_stake.assert_not_called()

    @pytest.mark.asyncio
    async def test_different_block_height_processes(self):
        """Different nonce/block_height is not a duplicate."""
        wallet = "allo1wallet"
        client = _make_mock_client_delegate_success()
        autostake = AutoStakeConfig(
            target_type=AutoStakeTargetType.REPUTER,
            target_address="allo1reputer",
        )

        event1 = _make_rewards_settled_event(block_height=1000, block_height_tx=1005)
        event1.addresses = [wallet]
        event1.rewards = ["500"]

        result1 = await process_autostake_rewards_settled(
            role=AutoStakeRole.INFERER,
            event=event1,
            topic_id=42,
            wallet_addr=wallet,
            client=client,
            autostake=autostake,
            default_fee_tier=Mock(value="PRIORITY"),
            last_autostake_key=None,
        )
        assert result1 is not None

        event2 = _make_rewards_settled_event(block_height=1001, block_height_tx=1006)
        event2.addresses = [wallet]
        event2.rewards = ["600"]

        result2 = await process_autostake_rewards_settled(
            role=AutoStakeRole.INFERER,
            event=event2,
            topic_id=42,
            wallet_addr=wallet,
            client=client,
            autostake=autostake,
            default_fee_tier=Mock(value="PRIORITY"),
            last_autostake_key=result1,
        )
        assert result2 is not None
        assert client.emissions.tx.delegate_stake.call_count == 2

    @pytest.mark.asyncio
    async def test_same_block_height_different_reward_processes(self):
        """Same block_height but different reward amount is not a duplicate."""
        wallet = "allo1wallet"
        client = _make_mock_client_delegate_success()
        autostake = AutoStakeConfig(
            target_type=AutoStakeTargetType.REPUTER,
            target_address="allo1reputer",
        )

        event1 = _make_rewards_settled_event(block_height=1000)
        event1.addresses = [wallet]
        event1.rewards = ["500"]

        result1 = await process_autostake_rewards_settled(
            role=AutoStakeRole.INFERER,
            event=event1,
            topic_id=42,
            wallet_addr=wallet,
            client=client,
            autostake=autostake,
            default_fee_tier=Mock(value="PRIORITY"),
            last_autostake_key=None,
        )
        assert result1 is not None

        event2 = _make_rewards_settled_event(block_height=1000, block_height_tx=1005)
        event2.addresses = [wallet]
        event2.rewards = ["501"]  # different reward

        result2 = await process_autostake_rewards_settled(
            role=AutoStakeRole.INFERER,
            event=event2,
            topic_id=42,
            wallet_addr=wallet,
            client=client,
            autostake=autostake,
            default_fee_tier=Mock(value="PRIORITY"),
            last_autostake_key=result1,
        )
        assert result2 is not None
        assert client.emissions.tx.delegate_stake.call_count == 2
