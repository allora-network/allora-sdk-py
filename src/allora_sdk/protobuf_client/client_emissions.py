from logging import Logger
import logging
from typing import Dict, List, Optional, Protocol, runtime_checkable
from cosmpy.aerial.client import TxResponse
from cosmpy.common.rest_client import RestClient
from allora_sdk.protobuf_client.protos.emissions.v3 import Nonce
from allora_sdk.protobuf_client.protos.emissions.v9 import (
    CanSubmitWorkerPayloadResponse,
    GetParamsRequest,
    GetParamsResponse,
    GetUnfulfilledWorkerNoncesRequest,
    CanSubmitWorkerPayloadRequest,
    GetUnfulfilledWorkerNoncesResponse,
    InputInference,
    InputInferenceForecastBundle,
    InputWorkerDataBundle,
    InsertWorkerPayloadRequest,
    InputForecastElement,
    InputForecast,
)
from allora_sdk.protobuf_client.tx_manager import FeeTier, TxError, TxManager

logger = logging.getLogger(__name__)


import allora_sdk.protobuf_client.protos.emissions.v9 as emissions_v9

@runtime_checkable
class EmissionsQueryLike(Protocol):
    def get_params(self, message: emissions_v9.GetParamsRequest) -> emissions_v9.GetParamsResponse: ...
    def get_unfulfilled_worker_nonces(self, message: emissions_v9.GetUnfulfilledWorkerNoncesRequest) -> emissions_v9.GetUnfulfilledWorkerNoncesResponse: ...
    def can_submit_worker_payload(self, message: emissions_v9.CanSubmitWorkerPayloadRequest) -> emissions_v9.CanSubmitWorkerPayloadResponse: ...


class EmissionsClient:
    """Client for Allora Emissions module operations."""

    def __init__(self, query_client: EmissionsQueryLike, tx_manager: Optional[TxManager]):
        self.query = query_client
        if tx_manager is not None:
            self.tx = EmissionsTxs(txs=tx_manager)

    # async def params(self):
    #     """Get emissions module parameters."""
    #     return await self.query.get_params(EmissionsGetParamsRequest())

    # async def get_unfulfilled_worker_nonces(self, topic_id: int):
    #     """Get nonces for which the inference window has not closed"""
    #     resp = await self.query.get_unfulfilled_worker_nonces(GetUnfulfilledWorkerNoncesRequest(topic_id=topic_id))
    #     nonces = [ x.block_height for x in resp.nonces.nonces ]
    #     return nonces

    # async def can_submit_worker_payload(self, worker: str, topic_id: int):
    #     resp = await self.query.can_submit_worker_payload(CanSubmitWorkerPayloadRequest(address=worker, topic_id=topic_id))
    #     return resp.can_submit_worker_payload

class EmissionsRestQueryClient(EmissionsQueryLike):
    """Emissions REST client."""

    API_URL = "/emissions/v9"

    def __init__(self, rest_api: RestClient):
        """
        Initialize authentication rest client.

        :param rest_api: RestClient api
        """
        self._rest_api = rest_api

    def get_params(self, message: GetParamsRequest):
        json_response = self._rest_api.get(f"{self.API_URL}/params")
        return GetParamsResponse().from_json(json_response)


    def get_unfulfilled_worker_nonces(self, message: GetUnfulfilledWorkerNoncesRequest):
        json_response = self._rest_api.get(f"{self.API_URL}/unfulfilled_worker_nonces/{message.topic_id}")
        return GetUnfulfilledWorkerNoncesResponse().from_json(json_response)

    def can_submit_worker_payload(self, message: CanSubmitWorkerPayloadRequest):
        json_response = self._rest_api.get(f"{self.API_URL}/can_submit_worker_payload/{message.topic_id}/{message.address}")
        return CanSubmitWorkerPayloadResponse().from_json(json_response)


class EmissionsTxs:
    def __init__(self, txs: TxManager):
        self._txs = txs

    async def insert_worker_payload(
        self,
        topic_id: int,
        inference_value: str,
        block_height: int,
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
            block_height: Block height for the inference
            forecast_elements: Optional list of forecast elements
                              [{"inferer": "address", "value": "prediction"}]
                              If None, worker will forecast its own inference value
            extra_data: Optional extra data as bytes
            proof: Optional proof string
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Manual gas limit override

        Returns:
            Transaction response with hash and status
        """
        if not self._txs:
            raise TxError('No wallet configured. Initialize client with private key or mnemonic.')

        worker_address = str(self._txs.wallet.address())

        inference = InputInference(
            topic_id=topic_id,
            block_height=block_height,
            inferer=worker_address,
            value=inference_value,
            extra_data=extra_data or b"",
            proof=proof or ""
        )

        # Always create forecast to satisfy blockchain validation
        if forecast_elements:
            # Use provided forecast elements for forecasting workers
            forecast_elems = [
                InputForecastElement(
                    inferer=elem["inferer"],
                    value=elem["value"]
                )
                for elem in forecast_elements
            ]
        else:
            # For inference-only workers: forecast their own inference value
            # This satisfies the blockchain validation requirement of >= 1 forecast element
            forecast_elems = [
                InputForecastElement(
                    inferer=worker_address,
                    value=inference_value
                )
            ]

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

        # sign bundle with pubkey using a 32-byte digest (secp256k1 requirement)
        import hashlib
        bundle_bytes = bytes(bundle)
        bundle_digest = hashlib.sha256(bundle_bytes).digest()
        bundle_sig = self._txs.wallet.signer().sign_digest(bundle_digest)

        worker_data_bundle = InputWorkerDataBundle(
            worker=worker_address,
            nonce=Nonce(block_height=block_height),
            topic_id=topic_id,
            inference_forecasts_bundle=bundle,
            inferences_forecasts_bundle_signature=bundle_sig,
            pubkey=self._txs.wallet.public_key().public_key_hex if self._txs.wallet.public_key() else ""
        )

        payload_request = InsertWorkerPayloadRequest(
            sender=worker_address,
            worker_data_bundle=worker_data_bundle
        )

        logger.info(f"🚀 Submitting worker payload for topic {topic_id}, inference: {inference_value}")
        logger.debug(f"   📋 Payload details: nonce={block_height}, forecaster={worker_address}")
        logger.debug(f"   📋 Forecast elements: {len(forecast_elems)} elements")
        if len(forecast_elems) == 1:
            elem = forecast_elems[0]
            logger.debug(f"   📋 Single forecast element: inferer={elem.inferer}, value={elem.value}")

        return await self._txs.submit_transaction(
            type_url="/emissions.v9.InsertWorkerPayloadRequest",
            msg=payload_request,
            gas_limit=gas_limit,
            fee_tier=fee_tier
        )
