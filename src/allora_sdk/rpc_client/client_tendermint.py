import logging
from allora_sdk.rpc_client.rest.cosmos_base_tendermint_v1beta1_rest_client import CosmosBaseTendermintV1Beta1ServiceLike
from allora_sdk.rpc_client.tx_manager import TxManager

logger = logging.getLogger("allora_sdk")


class TendermintClient:
    def __init__(self, query_client: CosmosBaseTendermintV1Beta1ServiceLike, tx_manager: TxManager | None = None):
        self.query = query_client
        self.tx = TendermintTxs(txs=tx_manager)

class TendermintTxs:
    def __init__(self, txs: TxManager | None):
        self._txs = txs

