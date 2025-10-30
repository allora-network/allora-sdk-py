import logging
from typing import Optional
from allora_sdk.protos.cosmos.base.tendermint.v1beta1 import (
    ServiceStub,
)
from allora_sdk.protos.cosmos.base.v1beta1 import Coin
from allora_sdk.rpc_client.tx_manager import FeeTier, TxManager

logger = logging.getLogger("allora_sdk")


class TendermintClient:
    def __init__(self, query_client: ServiceStub, tx_manager: TxManager | None = None):
        self.query = query_client
        self.tx = TendermintTxs(txs=tx_manager)

class TendermintTxs:
    def __init__(self, txs: TxManager | None):
        self._txs = txs

