import logging
from allora_sdk.rpc_client.rest.cosmos_auth_v1beta1_rest_client import CosmosAuthV1Beta1QueryLike
from allora_sdk.rpc_client.tx_manager import TxManager

logger = logging.getLogger("allora_sdk")


class AuthClient:
    def __init__(self, query_client: CosmosAuthV1Beta1QueryLike, tx_manager: TxManager | None = None):
        self.query = query_client
        self.tx = AuthTxs(txs=tx_manager)

class AuthTxs:
    def __init__(self, txs: TxManager | None):
        self._txs = txs

