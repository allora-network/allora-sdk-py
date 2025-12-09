from dataclasses import dataclass
import logging
from typing import Optional, Union
from allora_sdk.protos.cosmos.bank.v1beta1 import (
    MsgSend,
    QueryStub,
)
from allora_sdk.protos.cosmos.base.v1beta1 import Coin
from allora_sdk.rpc_client.tx_manager import FeeTier, TxManager, PendingTx

logger = logging.getLogger("allora_sdk")


class BankClient:
    def __init__(self, query_client: QueryStub, tx_manager: TxManager | None = None):
        self.query = query_client
        self.tx = BankTxs(txs=tx_manager)

@dataclass
class SendOutput:
    to_addr: str
    amount_uallo: str

class BankTxs:
    def __init__(self, txs: TxManager | None):
        self._txs = txs

    async def send(
        self,
        outputs: list[SendOutput],
        from_addr: Optional[str] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None,
        simulate: bool = False,
    ) -> Union[PendingTx, int]:
        """
        Send tokens to one or more recipients.
        
        Args:
            outputs: List of SendOutput objects specifying recipients and amounts
            from_addr: Optional sender address (defaults to wallet address)
            fee_tier: Fee tier to use (ECO, STANDARD, or PRIORITY)
            gas_limit: Optional gas limit (used only if simulate=False)
            simulate: If True, only simulate and return estimated gas (int).
                     If False, execute the transaction and return PendingTx.
            
        Returns:
            If simulate=True: Estimated gas units required (int)
            If simulate=False: PendingTx object that can be awaited for the transaction result
        """
        if self._txs is None:
            raise ValueError("TxManager is not initialized for BankTxs")

        msgs = [
            MsgSend(
                from_address=from_addr or str(self._txs.wallet.address()),
                to_address=o.to_addr,
                amount=[ Coin(denom="uallo", amount=o.amount_uallo) ],
            )
            for o in outputs
        ]
        
        if simulate:
            return await self._txs.simulate_transaction(
                type_url="/cosmos.bank.v1beta1.MsgSend",
                msgs=msgs,
            )
        else:
            return await self._txs.submit_transaction(
                type_url="/cosmos.bank.v1beta1.MsgSend",
                msgs=msgs,
                gas_limit=gas_limit,
                fee_tier=fee_tier,
            )


