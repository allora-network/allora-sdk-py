import logging
from cosmpy.aerial.wallet import LocalWallet
from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.protos.emissions.v9 import EventWorkerSubmissionWindowOpened
from allora_sdk.rpc_client.tx_manager import FeeTier, TxError
from allora_sdk.worker.types import AlreadySubmittedError, TRunFn, UseCase, WorkerResult
from allora_sdk.worker.utils import (
    can_submit_worker_payload,
    get_unfulfilled_worker_nonces,
    is_already_submitted_error,
    register_worker,
    resolve_maybe_awaitable,
)

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
    ):
        self.wallet = wallet
        self.client = client
        self.topic_id = topic_id
        self.forecast_fn = run
        self.fee_tier = fee_tier

    def name(self) -> str:
        return "forecaster"

    def submission_window_event_type(self):
        return EventWorkerSubmissionWindowOpened

    async def initialize(self) -> bool:
        return await register_worker(self.client, self.wallet, self.topic_id, FeeTier.PRIORITY)

    async def worker_is_whitelisted(self) -> bool:
        return await can_submit_worker_payload(self.client, self.wallet, self.topic_id)

    async def get_unfulfilled_nonces(self) -> set[int]:
        return await get_unfulfilled_worker_nonces(self.client, self.topic_id)

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
            if is_already_submitted_error(err):
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


