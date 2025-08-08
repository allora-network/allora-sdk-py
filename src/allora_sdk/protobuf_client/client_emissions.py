from logging import Logger
import logging
from typing import Dict, List, Optional
from cosmpy.aerial.client import LedgerClient, TxResponse
from cosmpy.aerial.wallet import LocalWallet
from grpclib.client import Channel
from allora_sdk.protobuf_client.config import AlloraNetworkConfig
from allora_sdk.protobuf_client.proto.emissions.v3 import Nonce
from allora_sdk.protobuf_client.proto.emissions.v9 import (
    QueryServiceStub as EmissionsQuerySvc,
    GetParamsRequest as EmissionsGetParamsRequest,
    InputInference,
    InputInferenceForecastBundle,
    InputWorkerDataBundle,
    InsertWorkerPayloadRequest,
    InputForecastElement,
    InputForecast,
)
from allora_sdk.protobuf_client.tx_manager import FeeTier, TxError, TxManager

logger = logging.getLogger(__name__)

class EmissionsClient:
    """Client for Allora Emissions module operations."""

    def __init__(self, client: LedgerClient, grpc_channel: Channel, wallet: LocalWallet | None, config: AlloraNetworkConfig):
        self.query = EmissionsQuerySvc(grpc_channel)
        if wallet is not None:
            self.txs = TxManager(wallet=wallet, client=client, config=config)
        self.client = client

    async def params(self):
        """Get emissions module parameters."""
        return await self.query.get_params(EmissionsGetParamsRequest())

    async def insert_worker_payload(
        self,
        topic_id: int,
        inference_value: str,
        block_height: Optional[int] = None,
        forecast_elements: Optional[List[Dict[str, str]]] = None,
        extra_data: Optional[bytes] = None,
        proof: Optional[str] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None
    ) -> TxResponse:
        """
        Submit a worker payload (inference/forecast) to the Allora network.

        Args:
            topic_id: The topic ID to submit inference for
            inference_value: The inference value as a string
            block_height: Block height for the inference (uses current if None)
            forecast_elements: Optional list of forecast elements
                              [{"inferer": "address", "value": "prediction"}]
            extra_data: Optional extra data as bytes
            proof: Optional proof string
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Manual gas limit override

        Returns:
            Transaction response with hash and status
        """
        if not self.txs:
            raise TxError('No wallet configured. Initialize client with private key or mnemonic.')

        # Get current block height if not provided
        if block_height is None:
            try:
                latest_block = self.client.query_latest_block()
                block_height = latest_block.height
                logger.info(f"Using current block height: {block_height}")
            except Exception as e:
                logger.error(f"Failed to get latest block height: {e}")
                raise TxError("Could not determine block height for inference")

        worker_address = str(self.txs.wallet.address())

        # Create InputInference
        inference = InputInference(
            topic_id=topic_id,
            block_height=block_height,
            inferer=worker_address,
            value=inference_value,
            extra_data=extra_data or b"",
            proof=proof or ""
        )

        if forecast_elements:
            forecast_elems = [
                InputForecastElement(
                    inferer=elem["inferer"],
                    value=elem["value"]
                )
                for elem in forecast_elements
            ]
        else:
            forecast_elems = []

        forecast = InputForecast(
            topic_id=topic_id,
            block_height=block_height,
            forecaster=worker_address,
            forecast_elements=forecast_elems,
            extra_data=extra_data or b""
        )

        bundle = InputInferenceForecastBundle(
            inference=inference,
            forecast=forecast,
        )
        # sign bundle with pubkey
        bundle_sig = self.txs.wallet.signer().sign_digest(bytes(bundle))

        # Create the complete message structure
        worker_data_bundle = InputWorkerDataBundle(
            worker=worker_address,
            nonce=Nonce(block_height=block_height),
            topic_id=topic_id,
            inference_forecasts_bundle=bundle,
            inferences_forecasts_bundle_signature=bundle_sig,
            pubkey=self.txs.wallet.public_key().public_key_hex if self.txs.wallet.public_key() else ""
        )

        payload_request = InsertWorkerPayloadRequest(
            sender=worker_address,
            worker_data_bundle=worker_data_bundle
        )

        logger.info(f"🚀 Submitting worker payload for topic {topic_id}, inference: {inference_value}")

        return await self.txs.submit_transaction(
            type_url="/emissions.v9.InsertWorkerPayloadRequest",
            msg=payload_request,
            gas_limit=gas_limit,
            fee_tier=fee_tier
        )
