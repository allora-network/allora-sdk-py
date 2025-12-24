from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Literal

from allora_sdk.rpc_client.protos.emissions.v9 import EventRewardsSettled
from allora_sdk.rpc_client.tx_manager import FeeTier

AutoStakeTargetType = Literal["reputer", "validator"]


@dataclass(frozen=True)
class AutoStakeConfig:
    """
    Configuration for automatically staking worker rewards.
    """

    target_type: AutoStakeTargetType
    target_address: str
    fee_tier: FeeTier | None = None
    fee_reserve_uallo: int = 0


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


