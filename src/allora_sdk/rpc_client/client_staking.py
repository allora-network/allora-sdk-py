import logging
from typing import Any, Optional

from allora_sdk.rpc_client.protos.cosmos.base.v1beta1 import Coin
from allora_sdk.rpc_client.protos.cosmos.staking.v1beta1 import MsgDelegate
from allora_sdk.rpc_client.tx_manager import FeeTier, PendingTx, TxManager

logger = logging.getLogger("allora_sdk")


class StakingClient:
    """
    Cosmos staking module client wrapper.

    Currently used for validator delegation (MsgDelegate) via TxManager.
    """

    def __init__(self, query_client: Any = None, tx_manager: TxManager | None = None):
        self.query = query_client
        self.tx = StakingTxs(txs=tx_manager)


class StakingTxs:
    def __init__(self, txs: TxManager | None):
        self._txs = txs

    async def delegate(
        self,
        *,
        validator_address: str,
        amount_uallo: int | str,
        delegator_address: Optional[str] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Delegate tokens from the delegator to the given validator.

        Args:
            validator_address: Validator operator address (e.g. allovaloper1...)
            amount_uallo: Amount to delegate (as int or str) in base denom units (uallo by default)
            delegator_address: Optional delegator address (defaults to wallet address)
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY)

        Returns:
            PendingTx object that can be awaited for the result
        """
        if self._txs is None:
            raise ValueError("TxManager is not initialized for StakingTxs")

        denom = self._txs.config.fee_denom
        logger.debug(
            "[STAKING] StakingTxs.delegate: delegator=%s validator=%s amount_uallo=%s denom=%s fee_tier=%s",
            delegator_address or str(self._txs.wallet.address()),
            validator_address,
            amount_uallo,
            denom,
            fee_tier.value,
        )
        msg = MsgDelegate(
            delegator_address=delegator_address or str(self._txs.wallet.address()),
            validator_address=validator_address,
            amount=Coin(denom=denom, amount=str(amount_uallo)),
        )

        return await self._txs.submit_transaction(
            type_url="/cosmos.staking.v1beta1.MsgDelegate",
            msgs=[msg],
            fee_tier=fee_tier,
        )
