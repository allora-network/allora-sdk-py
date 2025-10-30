from dataclasses import dataclass
import logging
from typing import Optional
from allora_sdk.protos.cosmos.bank.v1beta1 import (
    MsgSend,
    QueryStub,
)
from allora_sdk.protos.cosmos.base.v1beta1 import Coin
from allora_sdk.rpc_client.tx_manager import FeeTier, TxManager

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
    ):
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
        return await self._txs.submit_transaction(
            type_url="/cosmos.bank.v1beta1.MsgSend",
            msgs=msgs,
            gas_limit=gas_limit,
            fee_tier=fee_tier,
        )

