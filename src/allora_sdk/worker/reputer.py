from decimal import Decimal
import logging
import math
from typing import List, Optional, Union, Callable, Awaitable
from cosmpy.aerial.wallet import LocalWallet

from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.protos.emissions.v3 import Nonce, ReputerRequestNonce
from allora_sdk.rpc_client.protos.emissions.v10 import (
    CanSubmitReputerPayloadRequest,
    EventReputerSubmissionWindowOpened,
    GetNetworkInferencesAtBlockRequest,
    GetStakeFromReputerInTopicInSelfRequest,
    GetTopicRequest,
    GetUnfulfilledReputerNoncesRequest,
    IsReputerRegisteredInTopicIdRequest,
    NetworkInferenceBundle,
    LabeledValue,
)
from allora_sdk.rpc_client.protos.emissions.v9 import (
    InputOneOutInfererForecasterValues,
    InputValueBundle,
    InputWithheldWorkerAttributedValue,
    InputWorkerAttributedValue,
)
from allora_sdk.rpc_client.tx_manager import FeeTier, TxError
from allora_sdk.rpc_client.dec_canonical import canonicalize_dec
from allora_sdk.utils.format import uallo_to_allo
from allora_sdk.worker.types import AlreadySubmittedError, TRunFn, UseCase, WorkerResult, RunContext
from allora_sdk.worker.utils import resolve_maybe_awaitable

logger = logging.getLogger("allora_sdk")

ReputerFnResult = Union[str, float, Decimal]
ReputerFnAsync = Callable[[RunContext, dict[str, float]], Awaitable[ReputerFnResult]]
ReputerFnSync = Callable[[RunContext, dict[str, float]], ReputerFnResult]
ReputerFn = ReputerFnSync | ReputerFnAsync

class Reputer:
    """
    Reputer use-case: evaluates inference quality by computing losses between
    ground truth and on-chain network predictions.

    The reputer fetches network inferences for each nonce, computes a loss bundle
    using the configured (or auto-selected) loss function, and submits the result
    on-chain.  An optional stake top-up runs after each successful submission.

    Args:
        wallet: Signing wallet for transactions.
        client: RPC client for chain queries and transaction submission.
        topic_id: Allora topic to repute on.
        reputer_fn: Callback that takes an inference value and returns the loss.
        min_stake_uallo: If set, the reputer will top-up self-stake to at least this amount after each submission.
        fee_tier: Transaction fee tier (ECO / STANDARD / PRIORITY).
    """

    def __init__(
        self,
        wallet: LocalWallet,
        client: AlloraRPCClient,
        topic_id: int,
        reputer_fn: ReputerFn,
        min_stake_uallo: Optional[int] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
    ):
        self.wallet = wallet
        self.client = client
        self.topic_id = topic_id
        self.reputer_fn = reputer_fn
        self.min_stake_uallo = min_stake_uallo
        self.fee_tier = fee_tier


    def name(self) -> str:
        return "reputer"


    def submission_window_event_type(self):
        return EventReputerSubmissionWindowOpened


    async def initialize(self) -> bool:
        # Check if reputer is already registered
        resp = await self.client.emissions.query.is_reputer_registered_in_topic_id(
            IsReputerRegisteredInTopicIdRequest(
                topic_id=self.topic_id,
                address=str(self.wallet.address()),
            ),
        )

        if not resp.is_registered:
            logger.debug(f"   Registering reputer {str(self.wallet.address())} for topic {self.topic_id}")
            tx = await self.client.emissions.tx.register(
                topic_id=self.topic_id,
                owner_addr=str(self.wallet.address()),
                sender_addr=str(self.wallet.address()),
                is_reputer=True,
                fee_tier=self.fee_tier,
            )
            await tx.wait()

        try:
            await self._maybe_stake()
        except Exception as err:
            logger.error(f"Failed during initial stake fill up: {err}")

        return True


    async def worker_is_whitelisted(self) -> bool:
        can_submit_resp = await self.client.emissions.query.can_submit_reputer_payload(
            CanSubmitReputerPayloadRequest(
                address=str(self.wallet.address()),
                topic_id=self.topic_id,
            )
        )
        return can_submit_resp.can_submit_reputer_payload


    async def get_unfulfilled_nonces(self) -> set[int]:

        # GetUnfulfilledReputerNonces gives all epochs in flight, which is not what we want here
        # GetOpenReputerSubmissionWindows would be the more appropriate RPC call, but
        # it doesn't seem to be implemented in the rpc client
        # Returning [] here means we only react to EventReputerSubmissionWindowOpened
        return set()


    async def submit(self, nonce: int, account_seq: int) -> WorkerResult[InputValueBundle] | TxError | Exception:
        """Submit a reputer payload for the given nonce.

        Sequence-critical: payload is submitted first. Optional stake top-up runs
        only after successful payload submission, with fresh context, so it does
        not consume sequence before the core submission.
        """

        sender = str(self.wallet.address())

        # Fetch network inferences at block
        try:
            network_inferences_resp = await self.client.emissions.query.get_network_inferences_at_block(
                GetNetworkInferencesAtBlockRequest(
                    topic_id=self.topic_id,
                    block_height_last_inference=nonce,
                )
            )
            value_bundle = network_inferences_resp.network_inferences
            if value_bundle is None:
                logger.error(f"❌ No network inferences found at block {nonce}")
                return Exception(f"No network inferences found at block {nonce}")
        except Exception as err:
            logger.error(f"❌ Failed to get network inferences: {err}")
            return err

        # Compute loss bundle
        try:
            loss_bundle = await self._compute_loss_bundle(value_bundle)
        except Exception as err:
            logger.error(f"❌ Failed to compute loss bundle: {err}")
            return err

        reputer_request_nonce = ReputerRequestNonce(
            reputer_nonce=Nonce(block_height=nonce),
        )
        loss_bundle.reputer = sender

        try:
            pending = await self.client.emissions.tx.insert_reputer_payload(
                topic_id=self.topic_id,
                reputer_request_nonce=reputer_request_nonce,
                value_bundle=loss_bundle,
                fee_tier=self.fee_tier,
                account_seq=account_seq,
            )
            tx_resp = await pending.wait()
            result = WorkerResult(submission=loss_bundle, tx_result=tx_resp)

        except TxError as err:
            logger.error(f"❌ TX ERROR: {err}")
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
            logger.error(f"❌ Unexpected error submitting reputer payload: {err.__class__.__name__} {err}")
            return err

        # Optional stake top-up after successful payload; runs with fresh context
        # so it does not consume sequence before core submission.
        try:
            await self._maybe_stake()
        except Exception as err:
            logger.error(f"Failed during optional post-submit staking: {err}")

        return result

    async def _compute_loss_bundle(self, network_inference: NetworkInferenceBundle) -> InputValueBundle:
        """
        Compute loss bundle from ground truth and network inferences.

        Computes losses for combined, naive, inferer, forecaster, one-out, and one-in values.
        """

        async def compute_loss(values: list[LabeledValue]) -> str:
            try:
                predicted = {v.label_name: float(v.value) for v in values}
            except (ValueError, TypeError) as err:
                raise ValueError(f"invalid numeric value for loss computation: {values}") from err

            run_context = RunContext(
                nonce = network_inference.nonce,
                client = self.client,
                topic_id = self.topic_id
            )

            loss = await resolve_maybe_awaitable(self.reputer_fn, run_context, predicted)
            return canonicalize_dec(loss)

        if self.reputer_fn is None:
            raise ValueError("no reputer_fn configured")

        combined_loss = await compute_loss(network_inference.combined_value)
        naive_loss = await compute_loss(network_inference.naive_value)
        inferer_losses = [InputWorkerAttributedValue(worker=x.worker, value=await compute_loss(x.values)) for x in network_inference.inferer_values]
        forecaster_losses = [InputWorkerAttributedValue(worker=x.worker, value=await compute_loss(x.values)) for x in network_inference.forecaster_values]
        one_out_inferer_losses = [InputWithheldWorkerAttributedValue(worker=x.withheld_inferer, value = await compute_loss(x.combined_inference)) for x in network_inference.one_out_inferer_values]
        one_out_forecaster_losses = [InputWithheldWorkerAttributedValue(worker=x.withheld_forecaster, value = await compute_loss(x.combined_inference)) for x in network_inference.one_out_forecaster_values]
        one_in_forecaster_losses = [InputWorkerAttributedValue(worker=x.forecaster, value = await compute_loss(x.combined_inference)) for x in network_inference.one_in_forecaster_values]

        # Compute one-out inferer-forecaster losses
        # v10 flattens these: each OneOutInfererForecasterValue has .forecaster, .withheld_inferer, .combined_inference
        # Group by forecaster to produce InputOneOutInfererForecasterValues (which expects nested structure)
        ooifs: dict[str, dict[str, str]] = {}
        for ooif in network_inference.one_out_inferer_forecaster_values:
            if not ooif.forecaster in ooifs:
                oofs[ooif.forecaster] = {}
            ooifs[ooif.forecaster][ooif.withheld_inferer] = await compute_loss(ooif.combined_inference)

        one_out_inferer_forecaster_losses = [
            InputOneOutInfererForecasterValues(
                forecaster=f,
                one_out_inferer_values=InputWithheldWorkerAttributedValue(
                    worker=i,
                    value=v,
                ))
            for (f, x) in ooifs for (i, v) in x
        ]

        return InputValueBundle(
            topic_id=self.topic_id,
            combined_value=combined_loss,
            naive_value=naive_loss,
            inferer_values=inferer_losses,
            forecaster_values=forecaster_losses,
            one_out_inferer_values=one_out_inferer_losses,
            one_out_forecaster_values=one_out_forecaster_losses,
            one_in_forecaster_values=one_in_forecaster_losses,
            one_out_inferer_forecaster_values=one_out_inferer_forecaster_losses,
        )


    async def _maybe_stake(self):
        """
        Check current stake and top-up if below target.

        If min_stake_uallo is unset (None) or zero, skip staking.
        Otherwise, top-up only the delta needed to reach min_stake_uallo.
        """

        min_stake = self.min_stake_uallo
        if min_stake is None or min_stake == 0:
            return

        sender = str(self.wallet.address())

        # Query current stake
        try:
            stake_resp = await self.client.emissions.query.get_stake_from_reputer_in_topic_in_self(
                GetStakeFromReputerInTopicInSelfRequest(
                    reputer_address=sender,
                    topic_id=self.topic_id,
                )
            )
            current_stake = int(stake_resp.amount) if stake_resp.amount else 0
        except Exception as e:
            logger.warning(f"⚠️ Failed to query current stake: {e}. Skipping top-up.")
            return

        logger.info(f"   Reputer stake: current={uallo_to_allo(current_stake)} min_stake={uallo_to_allo(min_stake)}")

        if current_stake >= min_stake:
            logger.debug("   Stake above minimum requested stake, skipping adding stake.")
            return

        delta = min_stake - current_stake
        logger.info(f"   Stake below minimum requested stake, adding stake (delta={delta} uallo)")

        try:
            pending_tx = await self.client.emissions.tx.add_stake(
                topic_id=self.topic_id,
                amount=str(delta),
                fee_tier=self.fee_tier,
            )
            tx_resp = await pending_tx.wait()
            if tx_resp.code != 0:
                logger.error(f"❌ Failed to add stake: code={tx_resp.code} log={tx_resp.raw_log}")
            else:
                logger.info(f"✅ Successfully staked {uallo_to_allo(delta)} ALLO (tx={tx_resp.txhash})")

        except Exception as e:
            logger.error(f"Failed to add stake: {e}")


_implements: type[UseCase[EventReputerSubmissionWindowOpened, InputValueBundle]] = Reputer
