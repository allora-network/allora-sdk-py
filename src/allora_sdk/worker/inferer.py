import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Union

from cosmpy.aerial.wallet import LocalWallet

from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.protos.emissions.v9 import (
    CanSubmitWorkerPayloadRequest,
    EventWorkerSubmissionWindowOpened,
    EventRewardsSettled,
    GetLatestNetworkInferencesRequest,
    GetUnfulfilledWorkerNoncesRequest,
    IsWorkerRegisteredInTopicIdRequest,
)
from allora_sdk.rpc_client.tx_manager import FeeTier, TxError
from allora_sdk.worker.types import AlreadySubmittedError, StopQueue, TRunFn, UseCase, WorkerResult
from allora_sdk.worker.utils import resolve_maybe_awaitable
from allora_sdk.worker.autostake import AutoStakeConfig, AutoStakeRole, process_autostake_rewards_settled, validate_autostake_config

logger = logging.getLogger("allora_sdk")


@dataclass(frozen=True)
class SanityCheckConfig:
    """
    Configuration for the inferer sanity check that compares predictions against network consensus.

    Uses a throttle to avoid expensive RPC on every submit: cached network inferences are reused
    until the throttle interval elapses.

    Attributes:
        enabled: If False, sanity checks are skipped entirely.
        throttle_interval_seconds: Minimum seconds between RPC calls; cached data is reused within this interval.
    """

    enabled: bool = True
    throttle_interval_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.throttle_interval_seconds < 0:
            raise ValueError("throttle_interval_seconds must be >= 0")


TInfererRunFnResult = Union[str, float, Decimal]
TInfererRunFn = TRunFn[TInfererRunFnResult]


class Inferer:
    def __init__(
        self,
        wallet: LocalWallet,
        client: AlloraRPCClient,
        topic_id: int,
        run: TInfererRunFn,
        fee_tier: FeeTier,
        autostake: AutoStakeConfig | None = None,
        sanity_check: SanityCheckConfig | None = None,
    ):
        self.wallet = wallet
        self.client = client
        self.topic_id = topic_id
        self.predict_fn = run
        self.fee_tier = fee_tier
        self.autostake = autostake
        self.sanity_check = sanity_check or SanityCheckConfig()

        # Simple in-memory idempotence for rewards events
        self._last_autostake_key: tuple[int, int] | None = None

        # Throttle cache: (fetch_timestamp, inferer_values)
        self._sanity_cache: tuple[float, list[float]] | None = None


    def name(self) -> str:
        return "inferer"


    def submission_window_event_type(self):
        return EventWorkerSubmissionWindowOpened


    async def initialize(self) -> bool:
        # Validate auto stake config
        await validate_autostake_config(
            autostake=self.autostake,
            topic_id=self.topic_id,
            client=self.client,
        )

        # Check if worker is already registered
        resp = await self.client.emissions.query.is_worker_registered_in_topic_id(
            IsWorkerRegisteredInTopicIdRequest(
                topic_id=self.topic_id,
                address=str(self.wallet.address()),
            ),
        )
        if resp.is_registered:
            return False

        logger.debug(f"Registering inferer {str(self.wallet.address())} for topic {self.topic_id}")
        tx = await self.client.emissions.tx.register(
            topic_id=self.topic_id,
            owner_addr=str(self.wallet.address()),
            sender_addr=str(self.wallet.address()),
            is_reputer=False,
            fee_tier=FeeTier.PRIORITY,
        )
        await tx.wait()
        return True


    async def worker_is_whitelisted(self) -> bool:
        can_submit_resp = await self.client.emissions.query.can_submit_worker_payload(
            CanSubmitWorkerPayloadRequest(
                address=str(self.wallet.address()),
                topic_id=self.topic_id,
            )
        )
        return can_submit_resp.can_submit_worker_payload


    async def get_unfulfilled_nonces(self) -> set[int]:
        resp = await self.client.emissions.query.get_unfulfilled_worker_nonces(
            GetUnfulfilledWorkerNoncesRequest(topic_id=self.topic_id)
        )
        nonces = { x.block_height for x in resp.nonces.nonces } if resp.nonces is not None else set[int]()
        return nonces

    async def handle_rewards_settled(self, event: EventRewardsSettled, block_height: int | None = None) -> None:
        """
        Handle EventRewardsSettled and auto-stake this worker's reward amount.
        """
        new_key = await process_autostake_rewards_settled(
            role=AutoStakeRole.INFERER,
            event=event,
            topic_id=self.topic_id,
            wallet_addr=str(self.wallet.address()),
            client=self.client,
            autostake=self.autostake,
            default_fee_tier=self.fee_tier,
            last_autostake_key=self._last_autostake_key,
        )
        if new_key is not None:
            self._last_autostake_key = new_key


    async def submit(self, nonce: int, account_seq: int) -> WorkerResult[TInfererRunFnResult] | TxError | Exception:
        try:
            if self.predict_fn is None:
                return Exception("no predict fn configured")

            prediction = await resolve_maybe_awaitable(self.predict_fn, nonce)
        except Exception as err:
            logger.debug(f"Prediction function failed: {err}")
            return err

        # Sanity check prediction against network consensus (throttled; see SanityCheckConfig)
        if self.sanity_check.enabled:
            try:
                await self._sanity_check_submission(float(prediction))
            except (ValueError, TypeError):
                logger.debug(f"Could not convert prediction to float for sanity check: {prediction}")

        try:
            pending = await self.client.emissions.tx.insert_worker_payload(
                topic_id=self.topic_id,
                inference_value=str(prediction),
                nonce=nonce,
                fee_tier=self.fee_tier,
                account_seq=account_seq,
            )
            resp = await pending.wait()

            return WorkerResult(submission=prediction, tx_result=resp)

        except TxError as err:
            already_submitted = False
            if err.code in (68, 75, 78):
                already_submitted = True
            elif "already submitted" in (err.message or "").lower():
                already_submitted = True

            if already_submitted:
                return AlreadySubmittedError(
                    codespace=err.codespace,
                    code=err.code,
                    tx_hash=err.tx_hash,
                    message=err.message,
                )
            else:
                return err

        except Exception as err:
            return err


    def _sanity_check_with_values(self, prediction: float, inferer_values: list[float]) -> None:
        """Apply z-score analysis using pre-extracted inferer values."""
        if len(inferer_values) < 3:
            return

        mean = sum(inferer_values) / len(inferer_values)
        variance = sum((x - mean) ** 2 for x in inferer_values) / len(inferer_values)
        std_dev = variance ** 0.5

        if std_dev == 0:
            return

        z_score = abs((prediction - mean) / std_dev)

        if z_score > 3.0:
            logger.warning(
                f"⚠️⚠️⚠️  SANITY CHECK WARNING: Your prediction ({prediction:.6f}) is {z_score:.1f} "
                f"standard deviations from the network consensus (mean: {mean:.6f}, std: {std_dev:.6f}). "
                f"Please verify you're predicting the correct target variable and using the right units."
            )
        elif z_score > 2.0:
            logger.info(
                f"ℹ️  NOTICE: Your prediction ({prediction:.6f}) is {z_score:.1f} standard deviations "
                f"from consensus (mean: {mean:.6f}). This may indicate a contrarian view or potential issue."
            )

    async def _sanity_check_submission(self, prediction: float) -> None:
        """
        Sanity check user's prediction against network consensus using z-score analysis.

        Uses a throttle: network inferences are cached and reused until
        throttle_interval_seconds elapses, reducing RPC overhead on rapid submissions.

        Warns the user if their prediction is suspiciously far from the consensus,
        which could indicate they're predicting the wrong target variable or using
        incorrect units.

        Args:
            prediction: User's prediction value to check
        """
        try:
            now = time.monotonic()
            cache = self._sanity_cache
            use_cache = (
                cache is not None
                and (now - cache[0]) < self.sanity_check.throttle_interval_seconds
            )

            if use_cache:
                assert cache is not None
                inferer_values = cache[1]
            else:
                response = await self.client.emissions.query.get_latest_network_inferences(
                    GetLatestNetworkInferencesRequest(topic_id=self.topic_id)
                )

                if not response.network_inferences or not response.network_inferences.inferer_values:
                    self._sanity_cache = (now, [])
                    return

                inferer_values = []
                for inferer in response.network_inferences.inferer_values:
                    try:
                        inferer_values.append(float(inferer.value))
                    except (ValueError, TypeError):
                        continue

                # Cache all fetched values (including 0-2 values) so throttling still works.
                self._sanity_cache = (now, inferer_values)

            if len(inferer_values) >= 3:
                self._sanity_check_with_values(prediction, inferer_values)

        except Exception as e:
            # Don't let sanity check failures block submissions
            logger.debug(f"Sanity check failed (non-fatal): {e}")


_implements: type[UseCase[EventWorkerSubmissionWindowOpened, TInfererRunFnResult]] = Inferer

