import logging
from cosmpy.aerial.wallet import LocalWallet
from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.protos.emissions.v9 import (
    CanSubmitWorkerPayloadRequest,
    EventWorkerSubmissionWindowOpened,
    EventRewardsSettled,
    GetUnfulfilledWorkerNoncesRequest,
    IsWorkerRegisteredInTopicIdRequest,
)
from allora_sdk.rpc_client.tx_manager import FeeTier, TxError
from allora_sdk.worker.types import AlreadySubmittedError, TRunFn, UseCase, WorkerResult
from allora_sdk.worker.utils import resolve_maybe_awaitable
from allora_sdk.worker.autostake import AutoStakeConfig, AutoStakeRole, process_autostake_rewards_settled

logger = logging.getLogger("allora_sdk")


# (epoch/nonce) -> {inferer_address: predicted_value}
type TForecasterRunFnResult = dict[str, float]
type TForecasterRunFn = TRunFn[TForecasterRunFnResult]


class Forecaster:
    """
    Forecasters submit predictions for multiple inferers in a single transaction.

    The user-provided `forecast_fn` returns a dict mapping `inferer_address -> predicted_value`.
    The SDK converts this to the chain's `forecast_elements` format and submits it using
    `InsertWorkerPayloadRequest` (via `client.emissions.tx.insert_worker_payload`).
    """

    def __init__(
        self,
        wallet: LocalWallet,
        client: AlloraRPCClient,
        topic_id: int,
        run: TForecasterRunFn,
        fee_tier: FeeTier,
        autostake: AutoStakeConfig | None = None,
    ):
        self.wallet = wallet
        self.client = client
        self.topic_id = topic_id
        self.forecast_fn = run
        self.fee_tier = fee_tier
        self.autostake = autostake

        # Simple in-memory idempotence for rewards events
        self._last_autostake_key: tuple[int, int] | None = None

    def name(self) -> str:
        return "forecaster"

    def submission_window_event_type(self):
        return EventWorkerSubmissionWindowOpened

    async def initialize(self) -> bool:
        resp = await self.client.emissions.query.is_worker_registered_in_topic_id(
            IsWorkerRegisteredInTopicIdRequest(
                topic_id=self.topic_id,
                address=str(self.wallet.address()),
            ),
        )
        if resp.is_registered:
            return False

        logger.debug(f"Registering forecaster {str(self.wallet.address())} for topic {self.topic_id}")
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
        nonces = {x.block_height for x in resp.nonces.nonces} if resp.nonces is not None else set[int]()
        return nonces

    async def handle_rewards_settled(self, event: EventRewardsSettled, block_height: int | None = None) -> None:
        """
        Handle EventRewardsSettled and auto-stake this worker's reward amount.
        """
        new_key = await process_autostake_rewards_settled(
            role=AutoStakeRole.FORECASTER,
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

    async def submit(self, nonce: int, account_seq: int) -> WorkerResult[TForecasterRunFnResult] | TxError | Exception:
        try:
            if self.forecast_fn is None:
                return Exception("no forecast fn configured")

            forecasts_raw = await resolve_maybe_awaitable(self.forecast_fn, nonce)
        except Exception as err:
            logger.debug(f"Forecast function failed: {err}")
            return err

        try:
            if not forecasts_raw:
                logger.warning("Empty forecasts dict provided, skipping submission")
                return Exception("Empty forecasts dict")

            # Coerce/validate to stable {str: float} and keep a canonical ordering for signing.
            forecasts: dict[str, float] = {}
            for addr, pred in forecasts_raw.items():
                forecasts[str(addr)] = float(pred)

            # Convert to forecast_elements format: [{"inferer": addr, "value": str}, ...]
            forecast_elements = [
                {"inferer": addr, "value": str(pred)}
                for addr, pred in sorted(forecasts.items(), key=lambda kv: kv[0])
            ]

            # Calculate aggregate for inference_value (required field).
            aggregate = sum(forecasts.values()) / len(forecasts)

            resp = await self.client.emissions.tx.insert_worker_payload(
                topic_id=self.topic_id,
                inference_value=str(aggregate),
                nonce=nonce,
                forecast_elements=forecast_elements,
                fee_tier=self.fee_tier,
                account_seq=account_seq,
            )
            if isinstance(resp, int):
                raise ValueError('invariant violation: `resp` is an `int`, wanted `PendingTx`')
            resp = await resp.wait()

            return WorkerResult(submission=forecasts, tx_result=resp)

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
            return err

        except Exception as err:
            return err


_implements: type[UseCase[EventWorkerSubmissionWindowOpened, TForecasterRunFnResult]] = Forecaster


