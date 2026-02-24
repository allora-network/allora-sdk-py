from __future__ import annotations

from dataclasses import dataclass
import logging
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from enum import Enum
from typing import Any, Iterable

from allora_sdk.rpc_client.protos.emissions.v9 import (
    EventRewardsSettled,
    IsReputerRegisteredInTopicIdRequest,
)
from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.tx_manager import FeeTier

logger = logging.getLogger("allora_sdk")


class InvalidAutoStakeConfigError(Exception):
    """Raised when autostake configuration is invalid (e.g., reputer not registered, validator not found)."""

    def __init__(self, message: str, target_type: str, target_address: str):
        self.target_type = target_type
        self.target_address = target_address
        super().__init__(message)

class AutoStakeTargetType(str, Enum):
    REPUTER = "reputer"
    VALIDATOR = "validator"


class AutoStakeRole(str, Enum):
    INFERER = "inferer"
    FORECASTER = "forecaster"


_EXPECTED_ACTOR_TYPES_BY_ROLE: dict[AutoStakeRole, tuple[str, ...]] = {
    AutoStakeRole.INFERER: ("ACTOR_TYPE_INFERER_UNSPECIFIED",),
    AutoStakeRole.FORECASTER: ("ACTOR_TYPE_FORECASTER",),
}

@dataclass(frozen=True)
class AutoStakeConfig:
    """
    Configuration for automatically staking worker rewards.
    """

    target_type: AutoStakeTargetType
    target_address: str
    fee_tier: FeeTier | None = None
    fee_reserve_uallo: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.target_type, str):
            object.__setattr__(self, "target_type", AutoStakeTargetType(self.target_type))


async def validate_autostake_config(
    autostake: AutoStakeConfig | None,
    topic_id: int,
    client: AlloraRPCClient,
) -> None:
    """
    Validate autostake configuration at worker initialization.

    For reputer targets, verifies the reputer is registered in the topic.
    For validator targets, verifies the validator exists on-chain.

    Args:
        autostake: The autostake configuration to validate (None is valid - no-op)
        topic_id: The topic ID for reputer registration checks
        client: The RPC client for making queries

    Raises:
        InvalidAutoStakeConfigError: If the autostake target is invalid
    """
    if autostake is None:
        return

    if autostake.target_type == AutoStakeTargetType.REPUTER:
        resp = await client.emissions.query.is_reputer_registered_in_topic_id(
            IsReputerRegisteredInTopicIdRequest(
                topic_id=topic_id,
                address=autostake.target_address,
            )
        )
        if not resp.is_registered:
            raise InvalidAutoStakeConfigError(
                f"Autostake reputer '{autostake.target_address}' is not registered in topic {topic_id}. "
                f"Please ensure the reputer address is correct and registered before starting the worker.",
                target_type=autostake.target_type.value,
                target_address=autostake.target_address,
            )
        logger.debug(
            f"[AUTO-STAKE] Validated reputer {autostake.target_address} is registered in topic {topic_id}"
        )

    elif autostake.target_type == AutoStakeTargetType.VALIDATOR:
        if client.staking.query is None:
            logger.warning(
                "[AUTO-STAKE] Cannot validate validator (staking query not available via REST). "
                "Skipping validation - errors will occur at delegation time if validator is invalid."
            )
            return

        try:
            validator = await client.staking.query.validator(autostake.target_address)
            if validator is None:
                raise InvalidAutoStakeConfigError(
                    f"Autostake validator '{autostake.target_address}' not found on-chain. "
                    f"Please verify the validator operator address (allovaloper1...) is correct.",
                    target_type=autostake.target_type.value,
                    target_address=autostake.target_address,
                )
            logger.debug(
                f"[AUTO-STAKE] Validated validator {autostake.target_address} exists on-chain"
            )
        except InvalidAutoStakeConfigError:
            raise
        except Exception as e:
            raise InvalidAutoStakeConfigError(
                f"Failed to verify autostake validator '{autostake.target_address}': {e}",
                target_type=autostake.target_type.value,
                target_address=autostake.target_address,
            ) from e


def extract_reward_amount_uallo(event: EventRewardsSettled, wallet_addr: str) -> int | None:
    """
    Extract this wallet's reward amount from EventRewardsSettled.

    Returns:
        int reward amount in uallo, or None if wallet isn't present or parsing fails.
    """
    if wallet_addr not in event.addresses:
        return None
    try:
        idx = event.addresses.index(wallet_addr)
        if idx >= len(event.rewards):
            return None
        raw = event.rewards[idx]
    except (ValueError, IndexError, TypeError):
        return None

    # The chain emits rewards as Dec values; the actual payout is truncated to an Int
    # (see reward.Reward.SdkIntTrim()). For positive values, this is equivalent to
    # truncation toward zero.
    try:
        return int(raw)
    except (ValueError, TypeError):
        pass

    try:
        dec = Decimal(str(raw))
        if not dec.is_finite():
            return None
        truncated = dec.to_integral_value(rounding=ROUND_DOWN)
        return int(truncated)
    except (InvalidOperation, ValueError, TypeError):
        return None


def normalize_actor_type(actor_type: Any) -> str:
    """
    Normalize EventRewardsSettled.actor_type into a stable string for comparisons.

    The field may come from:
    - betterproto Enum types (use `.name`)
    - websocket-parsed strings (often quoted, e.g. '"ACTOR_TYPE_FORECASTER"')
    - Enum string representations (e.g. 'ActorType.FORECASTER')

    We canonicalize to the chain's proto-name form (e.g. "ACTOR_TYPE_FORECASTER").
    """
    name = getattr(actor_type, "name", None)
    s = name if isinstance(name, str) and name else str(actor_type)
    s = s.strip().strip('"').removeprefix("ActorType.")
    if s and not s.startswith("ACTOR_TYPE_"):
        s = f"ACTOR_TYPE_{s}"
    return s


def compute_stake_amount_uallo(reward_uallo: int, fee_reserve_uallo: int) -> int:
    """Compute how much reward can be staked after preserving fee reserve."""
    reserve = max(0, int(fee_reserve_uallo))
    return max(0, int(reward_uallo) - reserve)


async def process_autostake_rewards_settled(
    *,
    role: AutoStakeRole,
    event: EventRewardsSettled,
    topic_id: int,
    wallet_addr: str,
    client: AlloraRPCClient,
    autostake: AutoStakeConfig | None,
    default_fee_tier: FeeTier,
    last_autostake_key: tuple[int, int] | None,
) -> tuple[int, int] | None:
    """
    Shared autostake handler for Inferer/Forecaster workers.

    Returns:
        The new idempotence key (nonce_block_height, reward_uallo) if we decided to process this event
        (even if the tx fails), otherwise None.
    """

    if autostake is None:
        return None

    expected_actor_types: Iterable[str] = _EXPECTED_ACTOR_TYPES_BY_ROLE.get(role, ())
    actor_type_raw = normalize_actor_type(getattr(event, "actor_type", None))
    if actor_type_raw not in set(expected_actor_types):
        logger.debug(
            "[AUTO-STAKE] Skipping: actor_type mismatch (%s)",
            actor_type_raw,
        )
        return None

    if getattr(event, "topic_id", None) != topic_id:
        logger.debug("[AUTO-STAKE] Skipping: topic_id mismatch (%s != %s)", getattr(event, "topic_id", None), topic_id)
        return None

    reward_uallo = extract_reward_amount_uallo(event, wallet_addr)
    if reward_uallo is None or reward_uallo <= 0:
        logger.debug(
            "[AUTO-STAKE] Skipping: no positive reward found for %s (reward_uallo=%s)",
            wallet_addr,
            reward_uallo,
        )
        return None

    autostake_key = (int(getattr(event, "block_height", 0) or 0), reward_uallo)
    if last_autostake_key == autostake_key:
        logger.debug("[AUTO-STAKE] Skipping: duplicate event key=%s", autostake_key)
        return None

    fee_tier = autostake.fee_tier if autostake.fee_tier is not None else default_fee_tier
    stake_amount_uallo = compute_stake_amount_uallo(
        reward_uallo=reward_uallo,
        fee_reserve_uallo=autostake.fee_reserve_uallo,
    )
    if stake_amount_uallo <= 0:
        logger.info(
            "[AUTO-STAKE] Skipping: reward_uallo=%s is not enough after fee reserve_uallo=%s",
            reward_uallo,
            autostake.fee_reserve_uallo,
        )
        return None

    logger.info(
        "[AUTO-STAKE] %s rewards settled: topic=%s nonce=%s payout_height_tx=%s reward_uallo=%s stake_amount_uallo=%s target=%s:%s",
        role.value,
        topic_id,
        getattr(event, "block_height", None),
        getattr(event, "block_height_tx", None),
        reward_uallo,
        stake_amount_uallo,
        autostake.target_type.value,
        autostake.target_address,
    )

    try:
        if autostake.target_type == AutoStakeTargetType.REPUTER:
            logger.info(
                "[AUTO-STAKE] Delegating to reputer: reputer=%s amount_uallo=%s fee_tier=%s",
                autostake.target_address,
                stake_amount_uallo,
                fee_tier.value,
            )
            pending = await client.emissions.tx.delegate_stake(
                sender=wallet_addr,
                topic_id=topic_id,
                reputer=autostake.target_address,
                amount=str(stake_amount_uallo),
                fee_tier=fee_tier,
            )
            resp = await pending.wait()
            if resp.code != 0:
                logger.error(f"[AUTO-STAKE] DelegateStake failed: code={resp.code} log={resp.raw_log}")
            else:
                logger.info(f"[AUTO-STAKE] Delegated {stake_amount_uallo}uallo to reputer (tx={resp.txhash})")

        elif autostake.target_type == AutoStakeTargetType.VALIDATOR:
            logger.info(
                "[AUTO-STAKE] Delegating to validator: validator=%s amount_uallo=%s denom=%s fee_tier=%s",
                autostake.target_address,
                stake_amount_uallo,
                client.network.fee_denom,
                fee_tier.value,
            )
            pending = await client.staking.tx.delegate(
                validator_address=autostake.target_address,
                amount_uallo=stake_amount_uallo,
                delegator_address=wallet_addr,
                fee_tier=fee_tier,
            )
            resp = await pending.wait()
            if resp.code != 0:
                logger.error(f"[AUTO-STAKE] MsgDelegate failed: code={resp.code} log={resp.raw_log}")
            else:
                logger.info(f"[AUTO-STAKE] Delegated {stake_amount_uallo}uallo to validator (tx={resp.txhash})")

        else:
            raise ValueError(f"Unknown autostake target_type: {autostake.target_type}")

    except Exception as e:
        logger.error(f"[AUTO-STAKE] Failed to autostake rewards: {e}")
        return None

    return autostake_key

