from dataclasses import dataclass
import logging
from allora_sdk.rpc_client.tx_manager import TxManager
from allora_sdk.rpc_client.rest import FeemarketFeemarketV1QueryLike

logger = logging.getLogger("allora_sdk")


class FeemarketClient:
    def __init__(self, query_client: FeemarketFeemarketV1QueryLike, tx_manager: TxManager | None = None):
        self.query = query_client
        self.tx = FeemarketTxs(txs=tx_manager)

class FeemarketTxs:
    def __init__(self, txs: TxManager | None):
        self._txs = txs

