import logging
from allora_sdk.protos.cosmos.tx.v1beta1 import (
    ServiceStub,
)
from allora_sdk.rpc_client.tx_manager import FeeTier, TxManager

logger = logging.getLogger("allora_sdk")


class TxClient:
    def __init__(self, query_client: ServiceStub, tx_manager: TxManager | None = None):
        self.query = query_client
        self.tx = TxTxs(txs=tx_manager)

class TxTxs:
    def __init__(self, txs: TxManager | None):
        self._txs = txs

