import logging
from typing import Optional

from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.wallet import LocalWallet
from grpclib.client import Channel

from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.tx_manager import TxManager
from allora_sdk.rest.mint_v5_rest_client import MintV5QueryServiceLike

logger = logging.getLogger("allora_sdk")


class MintClient:
    def __init__(self, query_client: MintV5QueryServiceLike, tx_manager: Optional[TxManager] = None):
        self.query = query_client
        # if tx_manager is not None:
        #     self.tx = MintTxs(txs=tx_manager)
