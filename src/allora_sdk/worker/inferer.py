import asyncio
import logging
from decimal import Decimal

from cosmpy.aerial.wallet import LocalWallet

from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.protos.emissions.v9 import (
    CanSubmitWorkerPayloadRequest,
    EventWorkerSubmissionWindowOpened,
    GetLatestNetworkInferencesRequest,
    GetUnfulfilledWorkerNoncesRequest,
    IsWorkerRegisteredInTopicIdRequest,
)
from allora_sdk.rpc_client.tx_manager import FeeTier, TxError
from allora_sdk.worker.types import AlreadySubmittedError, StopQueue, TRunFn, UseCase, WorkerResult
from allora_sdk.worker.utils import resolve_maybe_awaitable

logger = logging.getLogger("allora_sdk")


type TInfererRunFnResult = str | float | Decimal
type TInfererRunFn = TRunFn[TInfererRunFnResult]


class Inferer:
    def __init__(
        self,
        wallet: LocalWallet,
        client: AlloraRPCClient,
        topic_id: int,
        run: TInfererRunFn,
        fee_tier: FeeTier,
    ):
        self.wallet = wallet
        self.client = client
        self.topic_id = topic_id
        self.predict_fn = run
        self.fee_tier = fee_tier


    def name(self) -> str:
        return "inferer"


    def submission_window_event_type(self):
        return EventWorkerSubmissionWindowOpened


    async def ensure_registered(self):
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
        if isinstance(tx, int):
            raise ValueError('invariant violation: `resp` is an `int`, wanted `PendingTx`')
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


    async def submit(self, nonce: int, account_seq: int) -> WorkerResult[TInfererRunFnResult] | TxError | Exception:
        try:
            if self.predict_fn is None:
                return Exception("no predict fn configured")

            prediction = await resolve_maybe_awaitable(self.predict_fn, nonce)
        except Exception as err:
            logger.debug(f"Prediction function failed: {err}")
            return err

        # Sanity check prediction against network consensus
        try:
            await self._sanity_check_submission(float(prediction))
        except (ValueError, TypeError):
            logger.debug(f"Could not convert prediction to float for sanity check: {prediction}")

        try:
            resp = await self.client.emissions.tx.insert_worker_payload(
                topic_id=self.topic_id,
                inference_value=str(prediction),
                nonce=nonce,
                fee_tier=self.fee_tier,
                account_seq=account_seq,
            )
            if isinstance(resp, int):
                raise ValueError('invariant violation: `resp` is an `int`, wanted `PendingTx`')
            resp = await resp.wait()

            return WorkerResult(submission=prediction, tx_result=resp)

        except TxError as err:
            already_submitted = False
            if err.code == 78 or err.code == 75: # already submitted
                already_submitted = True
            elif "inference already submitted" in err.message: # this is a different "already submitted" from allora-chain that has no error code, awesome
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


    async def _sanity_check_submission(self, prediction: float) -> None:
        """
        Sanity check user's prediction against network consensus using z-score analysis.

        Warns the user if their prediction is suspiciously far from the consensus,
        which could indicate they're predicting the wrong target variable or using
        incorrect units.

        Args:
            prediction: User's prediction value to check
        """
        try:
            # Query latest network inferences to get consensus
            response = await self.client.emissions.query.get_latest_network_inferences(
                GetLatestNetworkInferencesRequest(topic_id=self.topic_id)
            )

            if not response.network_inferences or not response.network_inferences.inferer_values:
                # Not enough data to perform sanity check
                return

            # Extract individual inferer values
            inferer_values = []
            for inferer in response.network_inferences.inferer_values:
                try:
                    inferer_values.append(float(inferer.value))
                except (ValueError, TypeError):
                    continue

            if len(inferer_values) < 3:
                # Need at least 3 values for meaningful statistics
                return

            # Calculate mean and standard deviation
            mean = sum(inferer_values) / len(inferer_values)
            variance = sum((x - mean) ** 2 for x in inferer_values) / len(inferer_values)
            std_dev = variance ** 0.5

            if std_dev == 0:
                # All predictions are identical, can't calculate z-score
                return

            # Calculate z-score
            z_score = abs((prediction - mean) / std_dev)

            # Warn if prediction is more than 3 standard deviations away
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

        except Exception as e:
            # Don't let sanity check failures block submissions
            logger.debug(f"Sanity check failed (non-fatal): {e}")


_implements: type[UseCase[EventWorkerSubmissionWindowOpened, TInfererRunFnResult]] = Inferer

