import logging

from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.wallet import LocalWallet
from grpclib.client import Channel

from allora_sdk.protobuf_client.config import AlloraNetworkConfig
from allora_sdk.protobuf_client.tx_manager import TxManager
from .proto.mint.v5 import (
    QueryServiceStub as MintQuerySvc,
    QueryServiceParamsRequest as MintGetParamsRequest,
    QueryServiceEmissionInfoRequest as MintEmissionInfoRequest,
    QueryServiceInflationRequest as MintInflationRequest,
)


logger = logging.getLogger(__name__)

# class MintClient:
#     def __init__(self, client: LedgerClient):
#         self.client = client
#         self.wallet = wallet
#         # if wallet:
#             # self.txs = TxManager(wallet=wallet, client=client, config=config, proto_client=proto_client)
#         self.query = MintQuerySvc(grpc_channel)

#     async def params(self):
#         return await self.query.params(MintGetParamsRequest())

#     async def emission_info(self):
#         return await self.query.emission_info(MintEmissionInfoRequest())

#     async def inflation(self):
#         return await self.query.inflation(MintInflationRequest())



