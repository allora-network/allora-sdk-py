import asyncio
from decimal import Decimal
import logging
from typing import Awaitable, Callable, List, Optional, Type, Union
from cosmpy.aerial.wallet import LocalWallet

from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.protos.emissions.v3 import Nonce, ReputerRequestNonce, ValueBundle
from allora_sdk.rpc_client.protos.emissions.v9 import (
    CanSubmitReputerPayloadRequest,
    EventReputerSubmissionWindowOpened,
    GetNetworkInferencesAtBlockRequest,
    GetStakeFromReputerInTopicInSelfRequest,
    GetUnfulfilledReputerNoncesRequest,
    InputOneOutInfererForecasterValues,
    InputValueBundle,
    InputWithheldWorkerAttributedValue,
    InputWorkerAttributedValue,
    IsReputerRegisteredInTopicIdRequest,
)
from allora_sdk.rpc_client.tx_manager import FeeTier, TxError
from allora_sdk.utils.format import uallo_to_allo
from allora_sdk.worker.types import AlreadySubmittedError, UseCase, WorkerResult
from allora_sdk.worker.utils import resolve_maybe_awaitable

logger = logging.getLogger("allora_sdk")

# (epoch) -> ground_truth
type GroundTruthFn = Callable[[int], str | float | Decimal] | Callable[[int], Awaitable[str | float | Decimal]]

# (ground_truth, predicted_value) -> loss
type LossFn = Callable[[float, float], float]

def default_squared_error_loss(ground_truth: float, predicted: float) -> float:
    """Default loss function using squared error."""
    return (ground_truth - predicted) ** 2


class Reputer:
    def __init__(
        self,
        wallet: LocalWallet,
        client: AlloraRPCClient,
        topic_id: int,
        ground_truth_fn: GroundTruthFn,
        loss_fn: LossFn,
        min_stake_uallo: Optional[int] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
    ):
        self.wallet = wallet
        self.client = client
        self.topic_id = topic_id
        self.ground_truth_fn = ground_truth_fn
        self.loss_fn = loss_fn
        self.min_stake_uallo = min_stake_uallo
        self.fee_tier = fee_tier


    def name(self) -> str:
        return "reputer"


    def submission_window_event_type(self):
        return EventReputerSubmissionWindowOpened


    async def ensure_registered(self):
        resp = await self.client.emissions.query.is_reputer_registered_in_topic_id(
            IsReputerRegisteredInTopicIdRequest(
                topic_id=self.topic_id,
                address=str(self.wallet.address()),
            ),
        )
        if resp.is_registered:
            return False

        logger.debug(f"   Registering reputer {str(self.wallet.address())} for topic {self.topic_id}")
        tx = await self.client.emissions.tx.register(
            topic_id=self.topic_id,
            owner_addr=str(self.wallet.address()),
            sender_addr=str(self.wallet.address()),
            is_reputer=True,
            fee_tier=self.fee_tier,
        )
        if isinstance(tx, int):
            raise ValueError('invariant violation: `resp` is an `int`, wanted `PendingTx`')
        await tx.wait()
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
        resp = await self.client.emissions.query.get_unfulfilled_reputer_nonces(
            GetUnfulfilledReputerNoncesRequest(topic_id=self.topic_id)
        )
        nonces = set[int]()
        if resp.nonces is not None:
            for nonce_item in resp.nonces.nonces:
                if nonce_item.reputer_nonce and nonce_item.reputer_nonce.block_height:
                    nonces.add(nonce_item.reputer_nonce.block_height)
        return nonces


    async def submit(self, nonce: int, account_seq: int) -> WorkerResult[InputValueBundle] | TxError | Exception:
        """Submit a reputer payload for the given nonce."""

        # Stake top-up if needed
        # await self._maybe_stake()

        sender = str(self.wallet.address())

        # Get ground truth from user callback
        try:
            if self.ground_truth_fn is None:
                return Exception("no ground truth fn configured")

            ground_truth_raw = await resolve_maybe_awaitable(self.ground_truth_fn, nonce)
            ground_truth = float(ground_truth_raw)
        except Exception as err:
            logger.error(f"❌ Ground truth function failed: {err}")
            return err

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
            loss_bundle = await self._compute_loss_bundle(ground_truth, value_bundle)
        except Exception as err:
            logger.error(f"❌ Failed to compute loss bundle: {err}")
            return err

        reputer_request_nonce = ReputerRequestNonce(
            reputer_nonce=Nonce(block_height=nonce),
        )
        loss_bundle.reputer_request_nonce = reputer_request_nonce
        loss_bundle.reputer = sender

        try:
            resp = await self.client.emissions.tx.insert_reputer_payload(
                topic_id=self.topic_id,
                reputer_request_nonce=reputer_request_nonce,
                value_bundle=loss_bundle,
                fee_tier=self.fee_tier,
                account_seq=account_seq,
            )
            if isinstance(resp, int):
                raise ValueError('invariant violation: `resp` is an `int`, wanted `PendingTx`')

            tx_resp = await resp.wait()
            return WorkerResult(submission=loss_bundle, tx_result=tx_resp)

        except TxError as err:
            logger.error(f"❌ TX ERROR: {err}")
            if err.code == 68:
                return AlreadySubmittedError(
                    codespace=err.codespace,
                    code=err.code,
                    tx_hash=err.tx_hash,
                    message=err.message,
                )
            else:
                return err

        except Exception as err:
            logger.error(f"❌ XYZZY: {err.__class__.__name__} {err}")
            return err

    async def _compute_loss_bundle(self, ground_truth: float, value_bundle: ValueBundle) -> InputValueBundle:
        """
        Compute loss bundle from ground truth and network inferences.

        Computes losses for combined, naive, inferer, forecaster, one-out, and one-in values.
        """
        async def compute_loss(value_str: str) -> str:
            try:
                predicted = float(value_str)
                loss = await resolve_maybe_awaitable(self.loss_fn, ground_truth, predicted)
                return str(loss)
            except (ValueError, TypeError):
                return "0"

        # Compute combined and naive losses
        combined_loss = await compute_loss(value_bundle.combined_value) if value_bundle.combined_value else "0"
        naive_loss = await compute_loss(value_bundle.naive_value) if value_bundle.naive_value else "0"

        # Compute inferer losses
        inferer_values: List[InputWorkerAttributedValue] = []
        if value_bundle.inferer_values:
            for iv in value_bundle.inferer_values:
                inferer_values.append(InputWorkerAttributedValue(
                    worker=iv.worker,
                    value=await compute_loss(iv.value) if iv.value else "0",
                ))

        # Compute forecaster losses
        forecaster_values: List[InputWorkerAttributedValue] = []
        if value_bundle.forecaster_values:
            for fv in value_bundle.forecaster_values:
                forecaster_values.append(InputWorkerAttributedValue(
                    worker=fv.worker,
                    value=await compute_loss(fv.value) if fv.value else "0",
                ))

        # Compute one-out inferer losses
        one_out_inferer_values: List[InputWithheldWorkerAttributedValue] = []
        if value_bundle.one_out_inferer_values:
            for ooi in value_bundle.one_out_inferer_values:
                one_out_inferer_values.append(InputWithheldWorkerAttributedValue(
                    worker=ooi.worker,
                    value=await compute_loss(ooi.value) if ooi.value else "0",
                ))

        # Compute one-out forecaster losses
        one_out_forecaster_values: List[InputWithheldWorkerAttributedValue] = []
        if value_bundle.one_out_forecaster_values:
            for oof in value_bundle.one_out_forecaster_values:
                one_out_forecaster_values.append(InputWithheldWorkerAttributedValue(
                    worker=oof.worker,
                    value=await compute_loss(oof.value) if oof.value else "0",
                ))

        # Compute one-in forecaster losses
        one_in_forecaster_values: List[InputWorkerAttributedValue] = []
        if value_bundle.one_in_forecaster_values:
            for oif in value_bundle.one_in_forecaster_values:
                one_in_forecaster_values.append(InputWorkerAttributedValue(
                    worker=oif.worker,
                    value=await compute_loss(oif.value) if oif.value else "0",
                ))

        # Compute one-out inferer-forecaster losses
        one_out_inferer_forecaster_values: List[InputOneOutInfererForecasterValues] = []
        if value_bundle.one_out_inferer_forecaster_values:
            for ooif in value_bundle.one_out_inferer_forecaster_values:
                one_out_losses: List[InputWithheldWorkerAttributedValue] = []
                if ooif.one_out_inferer_values:
                    for withheld in ooif.one_out_inferer_values:
                        one_out_losses.append(
                            InputWithheldWorkerAttributedValue(
                                worker=withheld.worker,
                                value=await compute_loss(withheld.value)
                                if withheld.value
                                else "0",
                            )
                        )

                one_out_inferer_forecaster_values.append(
                    InputOneOutInfererForecasterValues(
                        forecaster=ooif.forecaster,
                        one_out_inferer_values=one_out_losses,
                    )
                )

        return InputValueBundle(
            topic_id=self.topic_id,
            combined_value=combined_loss,
            naive_value=naive_loss,
            inferer_values=inferer_values,
            forecaster_values=forecaster_values,
            one_out_inferer_values=one_out_inferer_values,
            one_out_forecaster_values=one_out_forecaster_values,
            one_in_forecaster_values=one_in_forecaster_values,
            one_out_inferer_forecaster_values=one_out_inferer_forecaster_values,
        )


    async def _maybe_stake(self):
        """
        Check current stake and top-up if below target.

        If min_stake_uallo is unset (None) or zero, skip staking.
        Otherwise, top-up only the delta needed to reach min_stake_uallo.
        """
        min_stake = self.min_stake_uallo
        if min_stake is None or min_stake == 0:
            logger.warning("⚠️ No minimum stake configured in reputer, skipping adding stake.")
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
            logger.debug("    Stake above minimum requested stake, skipping adding stake.")
            return

        delta = min_stake - current_stake
        logger.info(f"Stake below minimum requested stake, adding stake (delta={delta} uallo)")

        try:
            pending_tx = await self.client.emissions.tx.add_stake(
                topic_id=self.topic_id,
                amount=delta,
                fee_tier=self.fee_tier,
            )
            if isinstance(pending_tx, int):
                raise ValueError('invariant violation: `resp` is an `int`, wanted `PendingTx`')

            tx_resp = await pending_tx.wait()
            if tx_resp.code != 0:
                logger.error(f"❌ Failed to add stake: code={tx_resp.code} log={tx_resp.raw_log}")
            else:
                logger.info(f"✅ Successfully staked {uallo_to_allo(delta)} ALLO (tx={tx_resp.txhash})")

        except Exception as e:
            logger.error(f"Failed to add stake: {e}")


_implements: type[UseCase[EventReputerSubmissionWindowOpened, InputValueBundle]] = Reputer


