import logging
from typing import Dict, List, Optional
from allora_sdk.protos.emissions.v3 import Nonce
from allora_sdk.protos.emissions.v9 import (
    InputInference,
    InputInferenceForecastBundle,
    InputWorkerDataBundle,
    InsertWorkerPayloadRequest,
    InputForecastElement,
    InputForecast,
    RegisterRequest,
)
from allora_sdk.rpc_client.tx_manager import FeeTier, TxManager
from allora_sdk.rest import EmissionsV9QueryServiceLike

logger = logging.getLogger("allora_sdk")


class EmissionsClient:
    def __init__(self, query_client: EmissionsV9QueryServiceLike, tx_manager: TxManager | None = None):
        self.query = query_client
        if tx_manager is not None:
            self.tx = EmissionsTxs(txs=tx_manager)

class EmissionsTxs:
    def __init__(self, txs: TxManager):
        self._txs = txs

    async def register(
        self,
        topic_id: int,
        owner_addr: str,
        sender_addr: str,
        is_reputer: bool,
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None,
    ):
        msg = RegisterRequest(
            topic_id=topic_id,
            owner=owner_addr,
            sender=sender_addr,
            is_reputer=is_reputer,
        )
        return await self._txs.submit_transaction(
            type_url="/emissions.v9.RegisterRequest",
            msg=msg,
            gas_limit=gas_limit,
            fee_tier=fee_tier
        )

    async def insert_worker_payload(
        self,
        topic_id: int,
        inference_value: str,
        nonce: int,
        forecast_elements: Optional[List[Dict[str, str]]] = None,
        extra_data: Optional[bytes] = None,
        proof: Optional[str] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None,
    ):
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
            raise Exception("No wallet configured. Initialize client with private key or mnemonic.")

        worker_address = str(self._txs.wallet.address())

        inference = InputInference(
            topic_id=topic_id,
            block_height=nonce,
            inferer=worker_address,
            value=inference_value,
            extra_data=extra_data or b"",
            proof=proof or ""
        )

        forecast = None
        if forecast_elements:
            forecast_elems = [
                InputForecastElement(
                    inferer=elem["inferer"],
                    value=elem["value"]
                )
                for elem in forecast_elements
            ]

            forecast = InputForecast(
                topic_id=topic_id,
                block_height=nonce,
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
            nonce=Nonce(block_height=nonce),
            topic_id=topic_id,
            inference_forecasts_bundle=bundle,
            inferences_forecasts_bundle_signature=bundle_sig,
            pubkey=self._txs.wallet.public_key().public_key_hex if self._txs.wallet.public_key() else ""
        )

        payload_request = InsertWorkerPayloadRequest(
            sender=worker_address,
            worker_data_bundle=worker_data_bundle
        )

        logger.debug(f"🚀 Submitting worker payload for topic {topic_id}, inference: {inference_value}")
        logger.debug(f"   📋 Payload details: nonce={nonce}, forecaster={worker_address}")

        return await self._txs.submit_transaction(
            type_url="/emissions.v9.InsertWorkerPayloadRequest",
            msg=payload_request,
            gas_limit=gas_limit,
            fee_tier=fee_tier
        )

