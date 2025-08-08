import logging
from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.wallet import LocalWallet
from grpclib.client import Channel
from allora_sdk.protobuf_client.config import AlloraNetworkConfig
from allora_sdk.protobuf_client.tx_manager import TxManager
from .proto.cosmos.bank.v1beta1 import (
    QueryStub as BankQuerySvc,
    QueryTotalSupplyRequest as BankTotalSupplyRequest,
)

logger = logging.getLogger(__name__)


class BankClient:
    def __init__(self, client: LedgerClient, grpc_channel: Channel, wallet: LocalWallet | None, config: AlloraNetworkConfig):
        self.client = client
        self.wallet = wallet
        if wallet:
            self.txs = TxManager(wallet=wallet, client=client, config=config)
        self.query = BankQuerySvc(grpc_channel)

    async def total_supply(self):
        return await self.query.total_supply(BankTotalSupplyRequest())



