import logging
from typing import Dict, List, Optional
from allora_sdk.protos.emissions.v3 import Nonce
from allora_sdk.protos.emissions.v9 import (
    BulkAddToTopicReputerWhitelistRequest,
    BulkAddToTopicWorkerWhitelistRequest,
    CreateNewTopicRequest,
    FundTopicRequest,
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
            msgs=[ msg ],
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
            msgs=[ payload_request ],
            gas_limit=gas_limit,
            fee_tier=fee_tier
        )

    async def create_topic(
        self,
        metadata: str,
        loss_method: str,
        epoch_length: int,
        ground_truth_lag: int,
        worker_submission_window: int,
        p_norm: str = "3.0",
        alpha_regret: str = "0.1",
        allow_negative: bool = False,
        epsilon: str = "0.01",
        merit_sortition_alpha: str = "0.1",
        active_inferer_quantile: str = "0.2",
        active_forecaster_quantile: str = "0.2",
        active_reputer_quantile: str = "0.2",
        enable_worker_whitelist: bool = False,
        enable_reputer_whitelist: bool = False,
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None,
    ):
        """
        Create a new topic on the Allora network.

        Args:
            metadata: Topic metadata (typically JSON with name, description, etc.)
            loss_method: Loss function method (e.g., "mse", "mae")
            epoch_length: Number of blocks per epoch
            ground_truth_lag: Blocks to wait for ground truth after epoch
            worker_submission_window: Blocks allowed for worker submissions
            p_norm: P-norm value for loss calculation (default "3.0")
            alpha_regret: Regret parameter (default "0.1")
            allow_negative: Allow negative inference values (default False)
            epsilon: Epsilon parameter for calculations (default "0.01")
            merit_sortition_alpha: Merit sortition alpha (default "0.1")
            active_inferer_quantile: Active inferer quantile threshold (default "0.2")
            active_forecaster_quantile: Active forecaster quantile threshold (default "0.2")
            active_reputer_quantile: Active reputer quantile threshold (default "0.2")
            enable_worker_whitelist: Require whitelist for workers (default False)
            enable_reputer_whitelist: Require whitelist for reputers (default False)
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Manual gas limit override

        Returns:
            Transaction response with hash and status. The topic_id is in the response events.
        """
        if not self._txs:
            raise Exception("No wallet configured. Initialize client with private key or mnemonic.")

        creator_address = str(self._txs.wallet.address())

        msg = CreateNewTopicRequest(
            creator=creator_address,
            metadata=metadata,
            loss_method=loss_method,
            epoch_length=epoch_length,
            ground_truth_lag=ground_truth_lag,
            p_norm=p_norm,
            alpha_regret=alpha_regret,
            allow_negative=allow_negative,
            epsilon=epsilon,
            worker_submission_window=worker_submission_window,
            merit_sortition_alpha=merit_sortition_alpha,
            active_inferer_quantile=active_inferer_quantile,
            active_forecaster_quantile=active_forecaster_quantile,
            active_reputer_quantile=active_reputer_quantile,
            enable_worker_whitelist=enable_worker_whitelist,
            enable_reputer_whitelist=enable_reputer_whitelist,
        )

        logger.debug(f"🚀 Creating new topic with metadata: {metadata}")

        return await self._txs.submit_transaction(
            type_url="/emissions.v9.CreateNewTopicRequest",
            msgs=[ msg ],
            gas_limit=gas_limit,
            fee_tier=fee_tier
        )

    async def fund_topic(
        self,
        topic_id: int,
        amount: str,
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None,
    ):
        """
        Fund a topic with ALLO tokens to incentivize inferences.

        Args:
            topic_id: The topic ID to fund
            amount: Amount of uallo to fund (e.g., "1000000" for 1 ALLO)
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Manual gas limit override

        Returns:
            Transaction response with hash and status.
        """
        if not self._txs:
            raise Exception("No wallet configured. Initialize client with private key or mnemonic.")

        sender_address = str(self._txs.wallet.address())

        msg = FundTopicRequest(
            sender=sender_address,
            topic_id=topic_id,
            amount=amount,
        )

        logger.debug(f"🚀 Funding topic {topic_id} with {amount} uallo")

        return await self._txs.submit_transaction(
            type_url="/emissions.v9.FundTopicRequest",
            msgs=[ msg ],
            gas_limit=gas_limit,
            fee_tier=fee_tier
        )

    async def bulk_add_to_topic_worker_whitelist(
        self,
        topic_id: int,
        addresses: List[str],
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None,
    ):
        """
        Add multiple addresses to a topic's worker whitelist.

        Args:
            topic_id: The topic ID to update
            addresses: List of wallet addresses to whitelist
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Manual gas limit override

        Returns:
            Transaction response with hash and status.
        """
        if not self._txs:
            raise Exception("No wallet configured. Initialize client with private key or mnemonic.")

        sender_address = str(self._txs.wallet.address())

        msg = BulkAddToTopicWorkerWhitelistRequest(
            sender=sender_address,
            topic_id=topic_id,
            addresses=addresses,
        )

        logger.debug(f"🚀 Adding {len(addresses)} addresses to topic {topic_id} worker whitelist")

        return await self._txs.submit_transaction(
            type_url="/emissions.v9.BulkAddToTopicWorkerWhitelistRequest",
            msgs=[ msg ],
            gas_limit=gas_limit,
            fee_tier=fee_tier
        )

    async def bulk_add_to_topic_reputer_whitelist(
        self,
        topic_id: int,
        addresses: List[str],
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None,
    ):
        """
        Add multiple addresses to a topic's reputer whitelist.

        Args:
            topic_id: The topic ID to update
            addresses: List of wallet addresses to whitelist
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Manual gas limit override

        Returns:
            Transaction response with hash and status.
        """
        if not self._txs:
            raise Exception("No wallet configured. Initialize client with private key or mnemonic.")

        sender_address = str(self._txs.wallet.address())

        msg = BulkAddToTopicReputerWhitelistRequest(
            sender=sender_address,
            topic_id=topic_id,
            addresses=addresses,
        )

        logger.debug(f"🚀 Adding {len(addresses)} addresses to topic {topic_id} reputer whitelist")

        return await self._txs.submit_transaction(
            type_url="/emissions.v9.BulkAddToTopicReputerWhitelistRequest",
            msgs=[ msg ],
            gas_limit=gas_limit,
            fee_tier=fee_tier
        )

