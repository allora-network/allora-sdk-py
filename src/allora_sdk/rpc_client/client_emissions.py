import hashlib
import logging
from typing import Any, Dict, List, Optional

from allora_sdk.rpc_client.compat import payload_permission
from allora_sdk.rpc_client.dec_canonical import canonicalize_dec
from allora_sdk.rpc_client.protos.emissions.v3 import Nonce, ReputerRequestNonce
from allora_sdk.rpc_client.protos.emissions.v9 import (
    InputReputerValueBundle,
    InputValueBundle,
)
from allora_sdk.rpc_client.protos.emissions.v10 import (
    AddStakeRequest,
    BulkAddToTopicForecasterWhitelistRequest,
    BulkAddToTopicReputerWhitelistRequest,
    BulkAddToTopicWorkerWhitelistRequest,
    BulkRemoveFromTopicForecasterWhitelistRequest,
    CanCreateTopicRequest,
    CreateNewTopicRequest,
    DelegateStakeRequest,
    DisableTopicForecasterWhitelistRequest,
    EnableTopicForecasterWhitelistRequest,
    FundTopicRequest,
    GetParamsRequest,
    GetTopicRequest,
    InputForecast,
    InputForecastElement,
    InputInference,
    InputInferenceForecastBundle,
    InputLabeledValue,
    InputWorkerDataBundle,
    InsertReputerPayloadRequest,
    InsertWorkerPayloadRequest,
    RegisterRequest,
    TopicOutputArity,
    TopicType,
    UpdateTopicRequest,
)
from allora_sdk.rpc_client.rest import EmissionsV10QueryServiceLike
from allora_sdk.rpc_client.tx_manager import (
    FeeTier,
    PendingTx,
    TxManager,
    WalletNotConfiguredError,
)

logger = logging.getLogger("allora_sdk")


def _forecast_has_duplicate_inferers(forecast_elements: List[Dict[str, str]]) -> bool:
    """True when the forecast names the same inferer more than once."""
    seen: set[str] = set()
    for elem in forecast_elements:
        inferer = elem.get("inferer", "")
        if inferer in seen:
            return True
        seen.add(inferer)
    return False


def _actor_addresses_from_values(values: Any) -> list[str]:
    """Collect actor addresses from a list of attributed-value rows."""
    if not values:
        return []
    addresses: list[str] = []
    for row in values:
        for attr in ("worker", "inferer", "forecaster", "reputer"):
            addr = getattr(row, attr, None)
            if addr:
                addresses.append(str(addr))
                break
    return addresses


def _has_duplicate_actors(addresses: list[str]) -> bool:
    return len(addresses) != len(set(addresses))


def _reputer_bundle_has_duplicate_actors(value_bundle: InputValueBundle) -> bool:
    """True when any actor-keyed slice on the reputer bundle repeats an address.

    Matches ``InputValueBundle.Validate``: each flat slice is keyed by
    ``.worker``, and ``one_out_inferer_forecaster_values`` is keyed by the
    outer ``.forecaster`` as well as the nested withheld workers.
    """
    slices = (
        getattr(value_bundle, "inferer_values", None),
        getattr(value_bundle, "forecaster_values", None),
        getattr(value_bundle, "one_out_inferer_values", None),
        getattr(value_bundle, "one_out_forecaster_values", None),
        getattr(value_bundle, "one_in_forecaster_values", None),
    )
    for values in slices:
        if _has_duplicate_actors(_actor_addresses_from_values(values)):
            return True

    nested = getattr(value_bundle, "one_out_inferer_forecaster_values", None) or []
    forecasters: list[str] = []
    for entry in nested:
        forecaster = getattr(entry, "forecaster", None)
        if forecaster:
            forecasters.append(str(forecaster))
        nested_values = getattr(entry, "one_out_inferer_values", None)
        if _has_duplicate_actors(_actor_addresses_from_values(nested_values)):
            return True
    return _has_duplicate_actors(forecasters)


class EmissionsClient:
    def __init__(
        self,
        query_client: EmissionsV10QueryServiceLike,
        tx_manager: TxManager | None = None,
    ):
        self.query = query_client
        if tx_manager is not None:
            self.tx = EmissionsTxs(txs=tx_manager, query=query_client)


class EmissionsTxs:
    def __init__(
        self, txs: TxManager, query: EmissionsV10QueryServiceLike | None = None
    ):
        self._txs = txs
        self._query = query

    def _require_wallet(self) -> str:
        if not self._txs or self._txs.wallet is None:
            raise WalletNotConfiguredError(
                "No wallet configured. Initialize client with private key or mnemonic."
            )
        return str(self._txs.wallet.address())

    async def _params_floor_ceiling(self) -> tuple[int, int]:
        """Return (min_top_inferers_to_reward, max_top_inferers_to_reward)."""
        if self._query is None:
            return (0, 0)
        resp = await self._query.get_params(GetParamsRequest())
        params = resp.params
        if params is None:
            return (0, 0)
        return (
            int(getattr(params, "min_top_inferers_to_reward", 0) or 0),
            int(getattr(params, "max_top_inferers_to_reward", 0) or 0),
        )

    async def _reject_cap_outside_live_range(self, cap: int, *, action: str) -> None:
        """Raise when ``cap`` is outside the live params range [min, max].

        Mirrors chain ``validateMaxTopInferersToReward``. A zero floor still
        enforces the ceiling; skip only when both bounds are unset (no query
        client or empty params).
        """
        floor, ceiling = await self._params_floor_ceiling()
        if floor <= 0 and ceiling <= 0:
            return
        if cap < floor or (ceiling > 0 and cap > ceiling):
            raise ValueError(
                f"Refusing {action}: max_top_inferers_to_reward={cap} is outside "
                f"the live range [{floor}, {ceiling}]"
            )

    async def register(
        self,
        topic_id: int,
        owner_addr: str,
        sender_addr: str,
        is_reputer: bool,
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Register as a worker or reputer for a topic.

        Args:
            topic_id: The topic ID to register for
            owner_addr: Owner address
            sender_addr: Sender address
            is_reputer: Whether registering as a reputer (True) or worker (False)
            fee_tier: Fee tier to use (ECO, STANDARD, or PRIORITY)

        Returns:
            PendingTx object that can be awaited for the result
        """
        msg = RegisterRequest(
            topic_id=topic_id,
            owner=owner_addr,
            sender=sender_addr,
            is_reputer=is_reputer,
        )

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.RegisterRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )

    async def insert_worker_payload(
        self,
        topic_id: int,
        inference_value: Optional[list[InputLabeledValue]],
        nonce: int,
        forecast_elements: Optional[List[Dict[str, str]]] = None,
        extra_data: Optional[bytes] = None,
        proof: Optional[str] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
        account_seq: Optional[int] = None,
        timeout_height: Optional[int] = None,
    ) -> PendingTx:
        """
        Submit a worker payload (inference/forecast) to the Allora network.

        A payload that carries both an inference and a forecast is rejected as a
        whole unless the signer holds both permissions. An empty inference list
        and an empty forecast list are treated as absent; if neither part is
        present the call logs and raises without broadcasting. A forecast the
        chain later drops for having fewer than two active inferers still returns
        as a successful transaction.

        Args:
            topic_id: The topic ID to submit inference for
            inference_value: Optional inference value. None or empty means no inference.
            nonce: Epoch nonce for the payload (must be > 0)
            forecast_elements: Optional list of forecast elements
                              [{"inferer": "address", "value": "prediction"}]
            extra_data: Optional extra data as bytes
            proof: Optional proof string
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            account_seq: Optional account sequence override
            timeout_height: Optional block height at which the tx expires; the chain
                rejects it once passed (0/None = no expiry)

        Returns:
            PendingTx object that can be awaited for the result
        """
        worker_address = self._require_wallet()

        has_inference = bool(inference_value)
        has_forecast = bool(forecast_elements)

        if not has_inference and not has_forecast:
            logger.error(
                "Refusing InsertWorkerPayload: neither inference nor forecast is present "
                "(topic=%s nonce=%s)",
                topic_id,
                nonce,
            )
            raise ValueError(
                "At least one of inference_value or forecast_elements must be provided."
            )

        if nonce <= 0:
            logger.error(
                "Refusing InsertWorkerPayload: nonce must be > 0 (topic=%s nonce=%s)",
                topic_id,
                nonce,
            )
            raise ValueError(
                f"Worker payload nonce must be greater than 0, got {nonce}"
            )

        if has_forecast and forecast_elements is not None:
            if _forecast_has_duplicate_inferers(forecast_elements):
                logger.error(
                    "Refusing InsertWorkerPayload: duplicate inferer in forecast_elements "
                    "(topic=%s nonce=%s)",
                    topic_id,
                    nonce,
                )
                raise ValueError("duplicate inferer in forecast")

        if self._query is not None:
            allowed = await payload_permission(
                self._query,
                worker_address,
                topic_id,
                has_inference=has_inference,
                has_forecast=has_forecast,
            )
            if not allowed:
                logger.error(
                    "Refusing InsertWorkerPayload: permission denied for present parts "
                    "(topic=%s address=%s has_inference=%s has_forecast=%s). "
                    "Inference and forecast each use a three-gate check; a gate that is "
                    "off allows everyone, and topic worker membership does not grant "
                    "forecast permission.",
                    topic_id,
                    worker_address,
                    has_inference,
                    has_forecast,
                )
                raise ValueError(
                    "Not permitted to submit the present parts of this worker payload"
                )

        inference = None
        if has_inference and inference_value is not None:
            inference = InputInference(
                topic_id=topic_id,
                block_height=nonce,
                inferer=worker_address,
                value="0",
                values=inference_value,
                extra_data=extra_data or b"",
                proof=proof or "",
            )

        forecast = None
        if has_forecast and forecast_elements is not None:
            forecast_elems = [
                InputForecastElement(
                    inferer=elem["inferer"],
                    value=canonicalize_dec(elem["value"]),
                )
                for elem in forecast_elements
            ]
            forecast = InputForecast(
                topic_id=topic_id,
                block_height=nonce,
                forecaster=worker_address,
                forecast_elements=forecast_elems,
                extra_data=extra_data or b"",
            )

        bundle = InputInferenceForecastBundle(
            inference=inference,
            forecast=forecast,
        )

        bundle_bytes = bytes(bundle)
        bundle_digest = hashlib.sha256(bundle_bytes).digest()
        bundle_sig = self._txs.wallet.signer().sign_digest(bundle_digest)

        worker_data_bundle = InputWorkerDataBundle(
            worker=worker_address,
            nonce=Nonce(block_height=nonce),
            topic_id=topic_id,
            inference_forecasts_bundle=bundle,
            inferences_forecasts_bundle_signature=bundle_sig,
            pubkey=(
                self._txs.wallet.public_key().public_key_hex
                if self._txs.wallet.public_key()
                else ""
            ),
        )

        payload_request = InsertWorkerPayloadRequest(
            sender=worker_address,
            worker_data_bundle=worker_data_bundle,
        )

        logger.debug(
            "Submitting worker payload for topic %s, inference: %s",
            topic_id,
            inference_value,
        )
        logger.debug("   Payload details: nonce=%s, worker=%s", nonce, worker_address)

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.InsertWorkerPayloadRequest",
            msgs=[payload_request],
            fee_tier=fee_tier,
            account_seq=account_seq,
            timeout_height=timeout_height,
        )

    async def delegate_stake(
        self,
        sender: str,
        topic_id: int,
        reputer: str,
        amount: str,
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Delegate stake to a reputer for a topic.

        Args:
            sender: Sender address
            topic_id: The topic ID
            reputer: Reputer address to delegate to
            amount: Amount to delegate (as string)
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY)

        Returns:
            PendingTx object that can be awaited for the result
        """
        msg = DelegateStakeRequest(
            sender=sender,
            topic_id=topic_id,
            reputer=reputer,
            amount=amount,
        )

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.DelegateStakeRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )

    async def add_stake(
        self,
        topic_id: int,
        amount: str,
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Add stake to a topic as a reputer.

        Args:
            topic_id: The topic ID to stake on
            amount: Amount of uallo to stake (as string)
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD

        Returns:
            PendingTx object that can be awaited for the result
        """
        sender_address = self._require_wallet()

        msg = AddStakeRequest(
            sender=sender_address,
            topic_id=topic_id,
            amount=amount,
        )

        logger.debug("Adding stake of %s uallo to topic %s", amount, topic_id)

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.AddStakeRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )

    async def insert_reputer_payload(
        self,
        topic_id: int,
        reputer_request_nonce: ReputerRequestNonce,
        value_bundle: InputValueBundle,
        fee_tier: FeeTier = FeeTier.STANDARD,
        account_seq: Optional[int] = None,
        timeout_height: Optional[int] = None,
    ) -> PendingTx:
        """
        Submit a reputer payload (loss bundle) to the Allora network.

        Note: This method mutates `value_bundle` in-place (sets reputer_request_nonce).

        Args:
            topic_id: The topic ID to submit the reputer payload for
            reputer_request_nonce: The reputer request nonce containing block height
            value_bundle: The computed loss bundle (will be mutated)
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            account_seq: Optional account sequence override
            timeout_height: Optional block height at which the tx expires; the chain
                rejects it once passed (0/None = no expiry)

        Returns:
            PendingTx object that can be awaited for the result
        """
        reputer_address = self._require_wallet()

        if _reputer_bundle_has_duplicate_actors(value_bundle):
            logger.error(
                "Refusing InsertReputerPayload: duplicate actor in value bundle "
                "(topic=%s)",
                topic_id,
            )
            raise ValueError("duplicate actor in reputer value bundle")

        value_bundle.reputer_request_nonce = reputer_request_nonce

        bundle_bytes = bytes(value_bundle)
        bundle_digest = hashlib.sha256(bundle_bytes).digest()
        bundle_sig = self._txs.wallet.signer().sign_digest(bundle_digest)

        reputer_value_bundle = InputReputerValueBundle(
            value_bundle=value_bundle,
            signature=bundle_sig,
            pubkey=(
                self._txs.wallet.public_key().public_key_hex
                if self._txs.wallet.public_key()
                else ""
            ),
        )

        payload_request = InsertReputerPayloadRequest(
            sender=reputer_address,
            reputer_value_bundle=reputer_value_bundle,
        )

        logger.debug("Submitting reputer payload for topic %s", topic_id)

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.InsertReputerPayloadRequest",
            msgs=[payload_request],
            fee_tier=fee_tier,
            account_seq=account_seq,
            timeout_height=timeout_height,
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
        c_norm: str = "0.75",
        topic_type: TopicType = TopicType(TopicType.REGRESSION),
        output_arity: TopicOutputArity = TopicOutputArity(TopicOutputArity.SINGLE),
        require_unity: bool = False,
        unity_tolerance: str = "0",
        max_labels_per_submission: int = 1,
        label_whitelist: Optional[List[str]] = None,
        label_default_value: str = "0",
        label_case_sensitive: bool = False,
        fee_tier: FeeTier = FeeTier.STANDARD,
        *,
        max_top_inferers_to_reward: int,
        enable_forecaster_whitelist: bool = False,
    ) -> PendingTx:
        """
        Create a new topic on the Allora network.

        ``max_top_inferers_to_reward`` is keyword-only and required. It must sit
        in the live ``[min_top_inferers_to_reward, max_top_inferers_to_reward]``
        range from ``get_params`` when the floor is positive. On a v0.17 gRPC
        node the new fields are ignored and the topic is created from the
        fields that chain knows. ``enable_forecaster_whitelist`` is independent
        of ``enable_worker_whitelist`` (default False).

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
            c_norm: C-norm value for loss calculation (default "0.75")
            topic_type: Whether the topic is REGRESSION or CLASSIFICATION (default REGRESSION)
            output_arity: Whether workers submit a SINGLE value or MULTI-label output (default SINGLE)
            require_unity: For MULTI-label topics, require submitted label values to sum to unity (default False)
            unity_tolerance: Allowed deviation from unity when require_unity is set (decimal string, default "0")
            max_labels_per_submission: Cap on labels per worker submission (for MULTI topics, default 1)
            label_whitelist: Label allowlist for MULTI topics; None/empty means unrestricted (default None)
            label_default_value: Default value for labels missing in an inference (decimal string, default "0")
            label_case_sensitive: Preserve label casing when True; when False labels are lowercased
                during canonicalization (default False)
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            max_top_inferers_to_reward: Per-topic inferer active-set cap (required)
            enable_forecaster_whitelist: Require whitelist for forecasters (default False)

        Returns:
            PendingTx object that can be awaited for the result
        """
        creator_address = self._require_wallet()

        if self._query is not None:
            can_create = await self._query.can_create_topic(
                CanCreateTopicRequest(address=creator_address)
            )
            if not can_create.can_create_topic:
                logger.error(
                    "Refusing CreateNewTopic: address %s is not permitted to create topics",
                    creator_address,
                )
                raise ValueError(
                    f"Address {creator_address} is not permitted to create topics"
                )

        await self._reject_cap_outside_live_range(
            max_top_inferers_to_reward, action="CreateNewTopic"
        )

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
            c_norm=c_norm,
            topic_type=topic_type,
            output_arity=output_arity,
            require_unity=require_unity,
            unity_tolerance=unity_tolerance,
            max_labels_per_submission=max_labels_per_submission,
            label_whitelist=label_whitelist or [],
            label_default_value=label_default_value,
            label_case_sensitive=label_case_sensitive,
            max_top_inferers_to_reward=max_top_inferers_to_reward,
            enable_forecaster_whitelist=enable_forecaster_whitelist,
        )

        logger.debug("Creating new topic with metadata: %s", metadata)

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.CreateNewTopicRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )

    async def update_topic(
        self,
        topic_id: int,
        *,
        metadata: Optional[str] = None,
        loss_method: Optional[str] = None,
        alpha_regret: Optional[str] = None,
        merit_sortition_alpha: Optional[str] = None,
        p_norm: Optional[str] = None,
        c_norm: Optional[str] = None,
        max_labels_per_submission: Optional[int] = None,
        label_whitelist: Optional[List[str]] = None,
        label_default_value: Optional[str] = None,
        max_top_inferers_to_reward: Optional[int] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Update a topic by sending a full replacement of the mutable field set.

        Loads the current topic, overlays the arguments the caller actually
        passed, and sends every mutable field. Omitted arguments keep the
        current values. An explicit ``label_whitelist=[]`` clears the allowlist.

        Mutable fields sent: metadata, loss_method, alpha_regret,
        merit_sortition_alpha, p_norm, c_norm, max_labels_per_submission,
        label_whitelist, label_default_value, max_top_inferers_to_reward.
        Epoch length, windows, quantiles, type/arity, and whitelist-enabled
        flags are preserved by the chain. ``label_case_sensitive`` is immutable.

        Changing merit_sortition_alpha, max_labels_per_submission,
        label_whitelist, label_default_value, or max_top_inferers_to_reward
        while a worker submission window is open is rejected with emissions
        code 67.

        On a v0.17 gRPC node the new ``max_top_inferers_to_reward`` field is
        ignored, so the topic cap does not change. A copied cap of 0 (from a
        v0.17 GetTopic that has no cap field) is still sent in that case.

        Args:
            topic_id: Topic to update
            metadata: New metadata, or None to keep the current value
            loss_method: New loss method, or None to keep the current value
            alpha_regret: New alpha_regret, or None to keep the current value
            merit_sortition_alpha: New merit_sortition_alpha, or None to keep
            p_norm: New p_norm, or None to keep the current value
            c_norm: New c_norm, or None to keep the current value
            max_labels_per_submission: New label cap, or None to keep
            label_whitelist: New allowlist; None keeps current, [] clears it
            label_default_value: New default, or None to keep
            max_top_inferers_to_reward: New inferer cap, or None to keep current
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY)

        Returns:
            PendingTx object that can be awaited for the result
        """
        sender_address = self._require_wallet()
        if self._query is None:
            raise ValueError("update_topic requires a query client")

        topic_resp = await self._query.get_topic(GetTopicRequest(topic_id=topic_id))
        topic = topic_resp.topic
        if topic is None:
            raise ValueError(f"Topic {topic_id} not found")

        if topic.creator != sender_address:
            logger.error(
                "Refusing UpdateTopic: sender %s is not the creator of topic %s",
                sender_address,
                topic_id,
            )
            raise ValueError(
                f"Sender {sender_address} is not permitted to update topic {topic_id}"
            )

        # label_whitelist: None means keep; any list (including empty) replaces.
        label_whitelist_passed = label_whitelist is not None

        cap = (
            max_top_inferers_to_reward
            if max_top_inferers_to_reward is not None
            else int(getattr(topic, "max_top_inferers_to_reward", 0) or 0)
        )
        await self._reject_cap_outside_live_range(cap, action="UpdateTopic")

        msg = UpdateTopicRequest(
            sender=sender_address,
            topic_id=topic_id,
            metadata=metadata if metadata is not None else topic.metadata,
            loss_method=loss_method if loss_method is not None else topic.loss_method,
            alpha_regret=(
                alpha_regret if alpha_regret is not None else topic.alpha_regret
            ),
            merit_sortition_alpha=(
                merit_sortition_alpha
                if merit_sortition_alpha is not None
                else topic.merit_sortition_alpha
            ),
            p_norm=p_norm if p_norm is not None else topic.p_norm,
            c_norm=c_norm if c_norm is not None else topic.c_norm,
            max_labels_per_submission=(
                max_labels_per_submission
                if max_labels_per_submission is not None
                else topic.max_labels_per_submission
            ),
            label_whitelist=(
                list(label_whitelist or [])
                if label_whitelist_passed
                else list(topic.label_whitelist or [])
            ),
            label_default_value=(
                label_default_value
                if label_default_value is not None
                else topic.label_default_value
            ),
            max_top_inferers_to_reward=cap,
        )

        logger.debug("Updating topic %s", topic_id)

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.UpdateTopicRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )

    async def fund_topic(
        self,
        topic_id: int,
        amount: str,
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Fund a topic with ALLO tokens to incentivize inferences.

        Args:
            topic_id: The topic ID to fund
            amount: Amount of uallo to fund (e.g., "1000000" for 1 ALLO)
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD

        Returns:
            PendingTx object that can be awaited for the result
        """
        sender_address = self._require_wallet()

        msg = FundTopicRequest(
            sender=sender_address,
            topic_id=topic_id,
            amount=amount,
        )

        logger.debug("Funding topic %s with %s uallo", topic_id, amount)

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.FundTopicRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )

    async def bulk_add_to_topic_worker_whitelist(
        self,
        topic_id: int,
        addresses: List[str],
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Add multiple addresses to a topic's worker whitelist.

        Args:
            topic_id: The topic ID to update
            addresses: List of wallet addresses to whitelist
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD

        Returns:
            PendingTx object that can be awaited for the result
        """
        sender_address = self._require_wallet()

        msg = BulkAddToTopicWorkerWhitelistRequest(
            sender=sender_address,
            topic_id=topic_id,
            addresses=addresses,
        )

        logger.debug(
            "Adding %s addresses to topic %s worker whitelist",
            len(addresses),
            topic_id,
        )

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.BulkAddToTopicWorkerWhitelistRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )

    async def bulk_add_to_topic_reputer_whitelist(
        self,
        topic_id: int,
        addresses: List[str],
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Add multiple addresses to a topic's reputer whitelist.

        Args:
            topic_id: The topic ID to update
            addresses: List of wallet addresses to whitelist
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD

        Returns:
            PendingTx object that can be awaited for the result
        """
        sender_address = self._require_wallet()

        msg = BulkAddToTopicReputerWhitelistRequest(
            sender=sender_address,
            topic_id=topic_id,
            addresses=addresses,
        )

        logger.debug(
            "Adding %s addresses to topic %s reputer whitelist",
            len(addresses),
            topic_id,
        )

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.BulkAddToTopicReputerWhitelistRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )

    async def bulk_add_to_topic_forecaster_whitelist(
        self,
        topic_id: int,
        addresses: List[str],
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Add multiple addresses to a topic's forecaster whitelist.

        Not available on chain v0.17.x.

        Args:
            topic_id: The topic ID to update
            addresses: List of wallet addresses to whitelist
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD

        Returns:
            PendingTx object that can be awaited for the result
        """
        sender_address = self._require_wallet()

        msg = BulkAddToTopicForecasterWhitelistRequest(
            sender=sender_address,
            topic_id=topic_id,
            addresses=addresses,
        )

        logger.debug(
            "Adding %s addresses to topic %s forecaster whitelist",
            len(addresses),
            topic_id,
        )

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.BulkAddToTopicForecasterWhitelistRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )

    async def bulk_remove_from_topic_forecaster_whitelist(
        self,
        topic_id: int,
        addresses: List[str],
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Remove multiple addresses from a topic's forecaster whitelist.

        Not available on chain v0.17.x.

        Args:
            topic_id: The topic ID to update
            addresses: List of wallet addresses to remove
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD

        Returns:
            PendingTx object that can be awaited for the result
        """
        sender_address = self._require_wallet()

        msg = BulkRemoveFromTopicForecasterWhitelistRequest(
            sender=sender_address,
            topic_id=topic_id,
            addresses=addresses,
        )

        logger.debug(
            "Removing %s addresses from topic %s forecaster whitelist",
            len(addresses),
            topic_id,
        )

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.BulkRemoveFromTopicForecasterWhitelistRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )

    async def enable_topic_forecaster_whitelist(
        self,
        topic_id: int,
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Enable the forecaster whitelist gate on a topic.

        Not available on chain v0.17.x.

        Args:
            topic_id: The topic ID to update
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD

        Returns:
            PendingTx object that can be awaited for the result
        """
        sender_address = self._require_wallet()

        msg = EnableTopicForecasterWhitelistRequest(
            sender=sender_address,
            topic_id=topic_id,
        )

        logger.debug("Enabling forecaster whitelist on topic %s", topic_id)

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.EnableTopicForecasterWhitelistRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )

    async def disable_topic_forecaster_whitelist(
        self,
        topic_id: int,
        fee_tier: FeeTier = FeeTier.STANDARD,
    ) -> PendingTx:
        """
        Disable the forecaster whitelist gate on a topic.

        Not available on chain v0.17.x.

        Args:
            topic_id: The topic ID to update
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD

        Returns:
            PendingTx object that can be awaited for the result
        """
        sender_address = self._require_wallet()

        msg = DisableTopicForecasterWhitelistRequest(
            sender=sender_address,
            topic_id=topic_id,
        )

        logger.debug("Disabling forecaster whitelist on topic %s", topic_id)

        return await self._txs.submit_transaction(
            type_url="/emissions.v10.DisableTopicForecasterWhitelistRequest",
            msgs=[msg],
            fee_tier=fee_tier,
        )
