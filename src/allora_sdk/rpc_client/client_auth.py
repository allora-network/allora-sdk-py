import logging
from allora_sdk.protos.cosmos.auth.v1beta1 import (
    QueryStub,
)
from allora_sdk.protos.cosmos.base.v1beta1 import Coin
from allora_sdk.rpc_client.tx_manager import FeeTier, TxManager

logger = logging.getLogger("allora_sdk")


class AuthClient:
    def __init__(self, query_client: QueryStub, tx_manager: TxManager | None = None):
        self.query = query_client
        self.tx = AuthTxs(txs=tx_manager)

class AuthTxs:
    def __init__(self, txs: TxManager | None):
        self._txs = txs

