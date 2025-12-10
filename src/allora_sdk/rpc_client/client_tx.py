import logging
from allora_sdk.rest.cosmos_tx_v1beta1_rest_client import CosmosTxV1Beta1ServiceLike
from allora_sdk.rpc_client.tx_manager import TxManager

logger = logging.getLogger("allora_sdk")


class TxClient:
    def __init__(self, query_client: CosmosTxV1Beta1ServiceLike, tx_manager: TxManager | None = None):
        self.query = query_client
        self.tx = TxTxs(txs=tx_manager)

class TxTxs:
    def __init__(self, txs: TxManager | None):
        self._txs = txs

