import hashlib
import logging
from typing import Dict, List, Optional, Union
from allora_sdk.rpc_client.protos.emissions.v3 import Nonce, ReputerRequestNonce
from allora_sdk.rpc_client.protos.emissions.v9 import (
    AddStakeRequest,
    BulkAddToTopicReputerWhitelistRequest,
    BulkAddToTopicWorkerWhitelistRequest,
    CreateNewTopicRequest,
    DelegateStakeRequest,
    FundTopicRequest,
    InputInference,
    InputInferenceForecastBundle,
    InputWorkerDataBundle,
    InsertWorkerPayloadRequest,
    InsertReputerPayloadRequest,
    InputReputerValueBundle,
    InputValueBundle,
    InputForecastElement,
    InputForecast,
    RegisterRequest,
    RewardDelegateStakeRequest,
)
from allora_sdk.rpc_client.tx_manager import FeeTier, TxManager, PendingTx
from allora_sdk.rpc_client.rest import EmissionsV9QueryServiceLike

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
        simulate: bool = False,
    ) -> Union[PendingTx, int]:
        """
        Register as a worker or reputer for a topic.

        Args:
            topic_id: The topic ID to register for
            owner_addr: Owner address
            sender_addr: Sender address
            is_reputer: Whether registering as a reputer (True) or worker (False)
            fee_tier: Fee tier to use (ECO, STANDARD, or PRIORITY)
            gas_limit: Optional gas limit (used only if simulate=False)
            simulate: If True, only simulate and return estimated gas (int).
                     If False, execute the transaction and return PendingTx.

        Returns:
            If simulate=True: Estimated gas units required (int)
            If simulate=False: PendingTx object that can be awaited for the result
        """
        msg = RegisterRequest(
            topic_id=topic_id,
            owner=owner_addr,
            sender=sender_addr,
            is_reputer=is_reputer,
        )

        if simulate:
            return await self._txs.simulate_transaction(
                type_url="/emissions.v9.RegisterRequest",
                msgs=[ msg ],
            )
        else:
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
        account_seq: Optional[int] = None,
        simulate: bool = False,
    ) -> Union[PendingTx, int]:
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
            gas_limit: Optional gas limit (used only if simulate=False)
            simulate: If True, only simulate and return estimated gas (int).
                     If False, execute the transaction and return PendingTx.

        Returns:
            If simulate=True: Estimated gas units required (int)
            If simulate=False: PendingTx object that can be awaited for the result
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

        logger.debug(f"Submitting worker payload for topic {topic_id}, inference: {inference_value}")
        logger.debug(f"   Payload details: nonce={nonce}, forecaster={worker_address}")

        if simulate:
            return await self._txs.simulate_transaction(
                type_url="/emissions.v9.InsertWorkerPayloadRequest",
                msgs=[ payload_request ],
            )
        else:
            return await self._txs.submit_transaction(
                type_url="/emissions.v9.InsertWorkerPayloadRequest",
                msgs=[ payload_request ],
                gas_limit=gas_limit,
                fee_tier=fee_tier,
                account_seq=account_seq,
            )


    async def delegate_stake(
        self,
        sender: str,
        topic_id: int,
        reputer: str,
        amount: str,
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None,
        simulate: bool = False,
    ) -> Union[PendingTx, int]:

        msg = DelegateStakeRequest(
            sender=sender,
            topic_id=topic_id,
            reputer=reputer,
            amount=amount,
        )

        if simulate:
            return await self._txs.simulate_transaction(
                type_url="/emissions.v9.DelegateStakeRequest",
                msgs=[ msg ],
            )
        else:
            return await self._txs.submit_transaction(
                type_url="/emissions.v9.DelegateStakeRequest",
                msgs=[ msg ],
                gas_limit=gas_limit,
                fee_tier=fee_tier
            )

    async def add_stake(
        self,
        topic_id: int,
        amount: int,
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None,
        simulate: bool = False,
    ) -> Union[PendingTx, int]:
        """
        Add stake to a topic as a reputer.

        Args:
            topic_id: The topic ID to stake on
            amount: Amount of uallo to stake
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Optional gas limit (used only if simulate=False)
            simulate: If True, only simulate and return estimated gas (int).
                     If False, execute the transaction and return PendingTx.

        Returns:
            If simulate=True: Estimated gas units required (int)
            If simulate=False: PendingTx object that can be awaited for the result
        """
        if not self._txs:
            raise Exception("No wallet configured. Initialize client with private key or mnemonic.")

        sender_address = str(self._txs.wallet.address())

        msg = AddStakeRequest(
            sender=sender_address,
            topic_id=topic_id,
            amount=str(amount),
        )

        logger.debug(f"Adding stake of {amount} uallo to topic {topic_id}")

        if simulate:
            return await self._txs.simulate_transaction(
                type_url="/emissions.v9.AddStakeRequest",
                msgs=[ msg ],
            )
        else:
            return await self._txs.submit_transaction(
                type_url="/emissions.v9.AddStakeRequest",
                msgs=[ msg ],
                gas_limit=gas_limit,
                fee_tier=fee_tier
            )

    async def reward_delegate_stake(
        self,
        topic_id: int,
        reputer: str,
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None,
        simulate: bool = False,
    ) -> Union[PendingTx, int]:
        """
        Claim and restake pending delegation rewards for a reputer.

        This transaction claims any pending rewards from delegate stake and
        automatically restakes them to the same reputer on the same topic.

        Args:
            topic_id: The topic ID where the reputer is staked
            reputer: The reputer address to claim rewards from
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Optional gas limit (used only if simulate=False)
            simulate: If True, only simulate and return estimated gas (int).
                     If False, execute the transaction and return PendingTx.

        Returns:
            If simulate=True: Estimated gas units required (int)
            If simulate=False: PendingTx object that can be awaited for the result
        """
        if not self._txs:
            raise Exception("No wallet configured. Initialize client with private key or mnemonic.")

        sender_address = str(self._txs.wallet.address())

        msg = RewardDelegateStakeRequest(
            sender=sender_address,
            topic_id=topic_id,
            reputer=reputer,
        )

        logger.debug(f"Claiming and restaking delegate rewards for topic {topic_id}, reputer {reputer}")

        if simulate:
            return await self._txs.simulate_transaction(
                type_url="/emissions.v9.RewardDelegateStakeRequest",
                msgs=[ msg ],
            )
        else:
            return await self._txs.submit_transaction(
                type_url="/emissions.v9.RewardDelegateStakeRequest",
                msgs=[ msg ],
                gas_limit=gas_limit,
                fee_tier=fee_tier
            )

    async def insert_reputer_payload(
        self,
        topic_id: int,
        reputer_request_nonce: ReputerRequestNonce,
        value_bundle: InputValueBundle,
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None,
        account_seq: Optional[int] = None,
        simulate: bool = False,
    ) -> Union[PendingTx, int]:
        """
        Submit a reputer payload (loss bundle) to the Allora network.

        Args:
            topic_id: The topic ID to submit the reputer payload for
            reputer_request_nonce: The reputer request nonce containing block height
            value_bundle: The computed loss bundle
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Optional gas limit (used only if simulate=False)
            simulate: If True, only simulate and return estimated gas (int).
                     If False, execute the transaction and return PendingTx.

        Returns:
            If simulate=True: Estimated gas units required (int)
            If simulate=False: PendingTx object that can be awaited for the result
        """
        if not self._txs:
            raise Exception("No wallet configured. Initialize client with private key or mnemonic.")

        reputer_address = str(self._txs.wallet.address())

        value_bundle.reputer_request_nonce = reputer_request_nonce

        # Sign the value bundle
        bundle_bytes = bytes(value_bundle)
        bundle_digest = hashlib.sha256(bundle_bytes).digest()
        bundle_sig = self._txs.wallet.signer().sign_digest(bundle_digest)

        reputer_value_bundle = InputReputerValueBundle(
            value_bundle=value_bundle,
            signature=bundle_sig,
            pubkey=self._txs.wallet.public_key().public_key_hex if self._txs.wallet.public_key() else "",
        )

        payload_request = InsertReputerPayloadRequest(
            sender=reputer_address,
            reputer_value_bundle=reputer_value_bundle,
        )

        logger.debug(f"Submitting reputer payload for topic {topic_id}")

        if simulate:
            return await self._txs.simulate_transaction(
                type_url="/emissions.v9.InsertReputerPayloadRequest",
                msgs=[ payload_request ],
            )
        else:
            return await self._txs.submit_transaction(
                type_url="/emissions.v9.InsertReputerPayloadRequest",
                msgs=[ payload_request ],
                gas_limit=gas_limit,
                fee_tier=fee_tier,
                account_seq=account_seq,
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
        simulate: bool = False,
    ) -> Union[PendingTx, int]:
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
            gas_limit: Optional gas limit (used only if simulate=False)
            simulate: If True, only simulate and return estimated gas (int).
                     If False, execute the transaction and return PendingTx.

        Returns:
            If simulate=True: Estimated gas units required (int)
            If simulate=False: PendingTx object that can be awaited for the result
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

        logger.debug(f"Creating new topic with metadata: {metadata}")

        if simulate:
            return await self._txs.simulate_transaction(
                type_url="/emissions.v9.CreateNewTopicRequest",
                msgs=[ msg ],
            )
        else:
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
        simulate: bool = False,
    ) -> Union[PendingTx, int]:
        """
        Fund a topic with ALLO tokens to incentivize inferences.

        Args:
            topic_id: The topic ID to fund
            amount: Amount of uallo to fund (e.g., "1000000" for 1 ALLO)
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Optional gas limit (used only if simulate=False)
            simulate: If True, only simulate and return estimated gas (int).
                     If False, execute the transaction and return PendingTx.

        Returns:
            If simulate=True: Estimated gas units required (int)
            If simulate=False: PendingTx object that can be awaited for the result
        """
        if not self._txs:
            raise Exception("No wallet configured. Initialize client with private key or mnemonic.")

        sender_address = str(self._txs.wallet.address())

        msg = FundTopicRequest(
            sender=sender_address,
            topic_id=topic_id,
            amount=amount,
        )

        logger.debug(f"Funding topic {topic_id} with {amount} uallo")

        if simulate:
            return await self._txs.simulate_transaction(
                type_url="/emissions.v9.FundTopicRequest",
                msgs=[ msg ],
            )
        else:
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
        simulate: bool = False,
    ) -> Union[PendingTx, int]:
        """
        Add multiple addresses to a topic's worker whitelist.

        Args:
            topic_id: The topic ID to update
            addresses: List of wallet addresses to whitelist
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Optional gas limit (used only if simulate=False)
            simulate: If True, only simulate and return estimated gas (int).
                     If False, execute the transaction and return PendingTx.

        Returns:
            If simulate=True: Estimated gas units required (int)
            If simulate=False: PendingTx object that can be awaited for the result
        """
        if not self._txs:
            raise Exception("No wallet configured. Initialize client with private key or mnemonic.")

        sender_address = str(self._txs.wallet.address())

        msg = BulkAddToTopicWorkerWhitelistRequest(
            sender=sender_address,
            topic_id=topic_id,
            addresses=addresses,
        )

        logger.debug(f"Adding {len(addresses)} addresses to topic {topic_id} worker whitelist")

        if simulate:
            return await self._txs.simulate_transaction(
                type_url="/emissions.v9.BulkAddToTopicWorkerWhitelistRequest",
                msgs=[ msg ],
            )
        else:
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
        simulate: bool = False,
    ) -> Union[PendingTx, int]:
        """
        Add multiple addresses to a topic's reputer whitelist.

        Args:
            topic_id: The topic ID to update
            addresses: List of wallet addresses to whitelist
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Optional gas limit (used only if simulate=False)
            simulate: If True, only simulate and return estimated gas (int).
                     If False, execute the transaction and return PendingTx.

        Returns:
            If simulate=True: Estimated gas units required (int)
            If simulate=False: PendingTx object that can be awaited for the result
        """
        if not self._txs:
            raise Exception("No wallet configured. Initialize client with private key or mnemonic.")

        sender_address = str(self._txs.wallet.address())

        msg = BulkAddToTopicReputerWhitelistRequest(
            sender=sender_address,
            topic_id=topic_id,
            addresses=addresses,
        )

        logger.debug(f"Adding {len(addresses)} addresses to topic {topic_id} reputer whitelist")

        if simulate:
            return await self._txs.simulate_transaction(
                type_url="/emissions.v9.BulkAddToTopicReputerWhitelistRequest",
                msgs=[ msg ],
            )
        else:
            return await self._txs.submit_transaction(
                type_url="/emissions.v9.BulkAddToTopicReputerWhitelistRequest",
                msgs=[ msg ],
                gas_limit=gas_limit,
                fee_tier=fee_tier
            )
