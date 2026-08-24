import asyncio
import math
from enum import Enum
import bech32
import traceback
import grpc
from datetime import datetime, timedelta
from decimal import Decimal
import logging
from typing import Any, Optional, Union, Dict, cast
from google.protobuf.message import Message

from cosmpy.aerial.wallet import Wallet
from cosmpy.crypto.address import Address
from cosmpy.aerial.tx import SigningCfg, Transaction, TxFee
from cosmpy.aerial.coins import Coin
from cosmpy.aerial.client.utils import ensure_timedelta
from cosmpy.protos.cosmos.tx.v1beta1.tx_pb2 import TxRaw as CosmpyTxRaw

from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.protos.cosmos.auth.v1beta1 import QueryAccountInfoRequest, QueryAccountRequest
from allora_sdk.rpc_client.protos.cosmos.bank.v1beta1 import QueryBalanceRequest
from allora_sdk.rpc_client.protos.cosmos.base.abci.v1beta1 import TxResponse
from allora_sdk.rpc_client.protos.cosmos.tx.v1beta1 import BroadcastMode, BroadcastTxRequest, GetTxRequest, SimulateRequest
from allora_sdk.rpc_client.protos.feemarket.feemarket.v1 import GasPriceRequest, StateRequest, ParamsRequest
from allora_sdk.rpc_client.interfaces import (
    CosmosAuthV1Beta1QueryLike,
    CosmosBankV1Beta1QueryLike,
    CosmosTxV1Beta1ServiceLike,
    FeemarketFeemarketV1QueryLike,
)

logger = logging.getLogger("allora_sdk")

class PendingTx:
    def __init__(
        self,
        manager: "TxManager",
        *,
        parent_tx_id: int,
        type_url: str,
        msgs: Any,
        fee_tier: "FeeTier",
        max_retries: int,
        timeout: Optional[timedelta],
    ):
        self.manager = manager
        self.parent_tx_id = parent_tx_id
        self.created_at = datetime.now()
        self.type_url = type_url
        self.msgs = msgs
        self.fee_tier = fee_tier
        self.max_retries = max_retries

        # These get populated during processing
        self.last_tx_hash: Optional[str] = None
        self.last_gas_limit: Optional[int] = None
        self.last_fee: Optional[Coin] = None
        # Classified error of the last confirmed-landed tx, valid only when the
        # most recent _bail_if_landed call returned RETRY_NEW_SEQUENCE — lets the
        # retry handler reuse details (e.g. gas_wanted) without re-querying the
        # hash. Cleared at the start of every _bail_if_landed call and after
        # being consumed, so it can never be mistaken for a fresh result.
        self.last_landed_error: Optional[Exception] = None
        self.start = datetime.now()
        self.timeout = timeout
        self.attempt: int = 0

        # Final outcome future: resolves to TxResponse or raises
        self._final_future: asyncio.Future[TxResponse] = asyncio.get_running_loop().create_future()
        # The detached submission task driving this tx (set by submit_transaction),
        # kept so TxManager.close() can cancel it and resolve the future.
        self._task: Optional["asyncio.Task"] = None
        # Set by the TxTimeoutError handler when it retries WITHOUT resetting the
        # account sequence (a deliberate same-sequence idempotency re-broadcast).
        # An AccountSequenceMismatchError rejection of such a re-broadcast is
        # near-certain proof the original tx landed — see that handler.
        self._same_seq_rebroadcast: bool = False

    async def wait(self) -> TxResponse:
        return await self._final_future

    def __await__(self):
        return self.wait().__await__()

class FeeTier(Enum):
    ECO      = "eco"
    STANDARD = "standard"
    PRIORITY = "priority"

class TxError(Exception):
    """Base exception for transaction errors."""
    def __init__(self, codespace: str, code: int, message: str, tx_hash: Optional[str] = None):
        super().__init__(message)
        self.codespace = codespace
        self.code = code
        self.message = message
        self.tx_hash = tx_hash

    def __str__(self):
        tx_info = f"tx_hash={self.tx_hash}" if self.tx_hash else "simulation"
        return f"TxError: codespace={self.codespace} code={self.code} {tx_info} {self.message}"

class InsufficientBalanceError(Exception):
    """Raised when account doesn't have enough balance for fees."""
    pass

class OutOfGasError(Exception):
    """Raised when transaction runs out of gas."""
    def __init__(self, message: str, gas_wanted: Optional[int] = None, gas_used: Optional[int] = None):
        super().__init__(message)
        self.gas_wanted = gas_wanted
        self.gas_used = gas_used

class InsufficientFeesError(Exception):
    pass

class AccountSequenceMismatchError(Exception):
    """Raised when account sequence is out of sync."""
    pass

class WalletNotConfiguredError(Exception):
    """Raised when a transaction method is called without a configured wallet."""
    pass

class MaxFeesExceededError(Exception):
    """Raised when a computed transaction fee exceeds the configured max_fees cap."""
    pass

class TxNotFoundError(Exception):
    pass

class TxTimeoutError(Exception):
    pass


# Errors the submission loop recovers from by re-attempting (fresh sequence /
# higher gas / refreshed fee). A tx that *landed* with one of these should be
# retried, not finalized — see TxManager._bail_if_landed.
_RETRYABLE_TX_ERRORS = (OutOfGasError, AccountSequenceMismatchError, InsufficientFeesError)


class LandedOutcome(Enum):
    """Result of checking whether the last broadcast already landed."""
    FINALIZED = "finalized"                    # future resolved; caller must return
    RETRY_NEW_SEQUENCE = "retry_new_sequence"  # landed retryably; retry with a fresh sequence
    NOT_LANDED = "not_landed"                  # not landed / unconfirmed; caller's normal path


# Backwards-compatible alias for the previously-private name.
_LandedOutcome = LandedOutcome

def _parse_fee_granter(fee_granter: Optional[str]) -> Optional[Address]:
    """Validate an optional bech32 fee-granter address with the `allo` prefix."""
    if fee_granter is None:
        return None

    if fee_granter != fee_granter.lower():
        raise ValueError(f"fee_granter must be lowercase bech32 (the chain's prefix check is case-sensitive): {fee_granter!r}")

    hrp, data = bech32.bech32_decode(fee_granter)
    if data is None:
        raise ValueError(f"fee_granter is not a valid bech32 address: {fee_granter!r}")
    if hrp != "allo":
        raise ValueError(f"fee_granter must have the 'allo' bech32 prefix, got {hrp!r}: {fee_granter!r}")

    # bech32_decode only verifies the checksum, so a well-formed but
    # wrong-length payload (including an empty one) gets this far. Cosmos
    # addresses are 20 bytes (secp256k1 account) or 32 bytes (module/
    # multisig). Catching it here beats an opaque fee error at broadcast.
    payload = bech32.convertbits(data, 5, 8, False)
    if payload is None or len(payload) not in (20, 32):
        length = "undecodable" if payload is None else f"{len(payload)} bytes"
        raise ValueError(f"fee_granter must decode to 20 or 32 bytes, got {length}: {fee_granter!r}")

    return Address(fee_granter)


class TxManager:
    def __init__(
        self,
        # Abstract cosmpy Wallet, not only LocalWallet, so a custodial/remote
        # signer (e.g. a Privy-backed wallet) can be injected and used here.
        wallet: Wallet,
        tx_client: CosmosTxV1Beta1ServiceLike,
        auth_client: CosmosAuthV1Beta1QueryLike,
        bank_client: CosmosBankV1Beta1QueryLike,
        feemarket_client: Optional[FeemarketFeemarketV1QueryLike],
        config: AlloraNetworkConfig,
        query_interval_secs: int = 2,
        query_timeout_secs: int = 10,
        fee_granter: Optional[str] = None,
        max_fees: Optional[int] = None,
        account_sequence_retry_delay: Optional[float] = None,
        gas_adjustment: Optional[float] = None,
        base_gas: Optional[int] = None,
        simulate_gas_from_start: Optional[bool] = None,
    ):
        if max_fees is not None and max_fees <= 0:
            raise ValueError(f"max_fees must be a positive integer, got {max_fees}")
        # isfinite before the comparison: NaN fails every ordering test, so a
        # bare `< 0` / `<= 0` waves it through to blow up later inside int()
        # or asyncio.sleep(). inf gets the same treatment.
        if account_sequence_retry_delay is not None and (
            not math.isfinite(account_sequence_retry_delay) or account_sequence_retry_delay < 0
        ):
            raise ValueError(f"account_sequence_retry_delay must be a finite value >= 0, got {account_sequence_retry_delay}")
        if gas_adjustment is not None and (not math.isfinite(gas_adjustment) or gas_adjustment <= 0):
            raise ValueError(f"gas_adjustment must be a finite value > 0, got {gas_adjustment}")
        if base_gas is not None and base_gas <= 0:
            raise ValueError(f"base_gas must be a positive integer, got {base_gas}")

        self.wallet = wallet
        self.fee_granter: Optional[Address] = _parse_fee_granter(fee_granter)
        self.max_fees = max_fees
        self.account_sequence_retry_delay = account_sequence_retry_delay
        # Applies to the initial gas estimate only. The per-attempt retry
        # escalation (gas_multiplier / fee_multiplier in _attempt_submissions)
        # is deliberately independent: raising gas_adjustment shifts the
        # starting point, it does not widen the retry ladder on top of it.
        self.gas_adjustment: float = gas_adjustment if gas_adjustment is not None else 1.2
        # gas_adjustment is applied on top of this too, so BASE_GAS=500000
        # with the default 1.2 adjustment yields gasWanted 600000.
        self.base_gas = base_gas
        self.simulate_gas_from_start: bool = simulate_gas_from_start if simulate_gas_from_start is not None else True
        self.tx_client = tx_client
        self.auth_client = auth_client
        self.bank_client = bank_client
        self.feemarket_client = feemarket_client
        self.config = config
        self.query_interval_secs = query_interval_secs
        self.query_timeout_secs = query_timeout_secs
        self.parent_tx_id = 0
        self._parent_tx_id_lock = asyncio.Lock()

        self._default_gas_limits = {
            "/emissions.v10.InsertWorkerPayloadRequest": 250000,
            "/emissions.v10.CreateNewTopicRequest": 300000,
            "/emissions.v10.FundTopicRequest": 150000,
            "/emissions.v10.BulkAddToTopicWorkerWhitelistRequest": 200000,
            "/emissions.v10.BulkAddToTopicReputerWhitelistRequest": 200000,
            "/cosmos.bank.v1beta1.MsgSend": 250000,
            "/cosmos.staking.v1beta1.MsgDelegate": 100000,
            "/cosmos.staking.v1beta1.MsgUndelegate": 100000,
        }

        self._fee_multipliers = {
            FeeTier.ECO: 1.0,        # Minimum fees
            FeeTier.STANDARD: 1.5,   # 50% higher than minimum
            FeeTier.PRIORITY: 2.5,   # 150% higher than minimum
        }

        # Strong references to in-flight submission tasks (see submit()) so the
        # event loop can't garbage-collect them mid-flight.
        self._inflight: set[PendingTx] = set()

        # Pending tx hash watchers monitored by a background task
        self._pending_attempts: Dict[str, Dict[str, Any]] = {}
        self._monitor_task: Optional[asyncio.Task] = None
        self._monitor_cond = asyncio.Condition()

        # Gas price caching
        self._cached_gas_price: Optional[Decimal] = None
        self._gas_price_cache_time: Optional[datetime] = None
        self._gas_price_cache_ttl_secs: int = config.gas_price_cache_ttl_secs
        # Base gas headroom applied to the simulated estimate on the first
        # attempt (see AlloraNetworkConfig.gas_adjustment). Default 1.0 = no
        # change from prior behavior. getattr keeps older externally-constructed
        # configs working.
        self._gas_adjustment: float = getattr(config, "gas_adjustment", 1.0)

    async def submit_transaction(
        self,
        type_url: str,
        msgs: list[Any],
        fee_tier: FeeTier = FeeTier.STANDARD,
        max_retries: int = 2,
        timeout: Optional[timedelta] = None,
        account_seq: Optional[int] = None,
    ) -> "PendingTx":
        if self.wallet is None:
            raise Exception('No wallet configured. Initialize client with private key or mnemonic.')

        estimated_gas_limit: Optional[int] = None
        if self.simulate_gas_from_start:
            try:
                estimated_gas_limit = await self.simulate_transaction(type_url, msgs)
                logger.debug(f"Simulated gas requirement for {type_url}: {estimated_gas_limit}")
                fee_preview = await self._calculate_optimal_fee(
                    estimated_gas_limit,
                    self._fee_multipliers[fee_tier],
                )
                logger.debug(f"Estimated fee for {type_url}: {fee_preview.amount} {fee_preview.denom}")
            except MaxFeesExceededError:
                raise
            except Exception as e:
                logger.debug(f"Unable to simulate transaction for gas estimate, falling back to defaults: {e}")
                estimated_gas_limit = None

        async with self._parent_tx_id_lock:
            next_parent_tx_id = self.parent_tx_id
            self.parent_tx_id += 1

        pending = PendingTx(
            manager=self,
            parent_tx_id=next_parent_tx_id,
            type_url=type_url,
            msgs=msgs,
            fee_tier=fee_tier,
            max_retries=max_retries,
            timeout=timeout,
        )

        # Kick off processing as a background task; caller can await the PendingTx.
        # Keep a strong reference until it finishes — asyncio holds only a weak
        # reference to bare tasks, so an unreferenced one can be garbage-collected
        # mid-flight, leaving pending._final_future unresolved forever.
        task = asyncio.create_task(
            self._attempt_submissions(pending, estimated_gas_limit, account_seq=account_seq)
        )
        pending._task = task
        self._inflight.add(pending)
        task.add_done_callback(lambda _: self._inflight.discard(pending))

        return pending

    async def close(self) -> None:
        """Cancel any in-flight submission tasks and wait for them to unwind.

        Gives the manager the same shutdown discipline the websocket subscriber
        has: if the client is dropped without awaiting pending transactions, the
        detached submission tasks would otherwise keep running. Idempotent.
        """
        pendings = list(self._inflight)
        tasks = [ p._task for p in pendings if p._task is not None ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Cancelling a submission task raises CancelledError inside
        # _attempt_submissions; being BaseException it bypasses the result-setting
        # handlers, so _final_future would be left unresolved and any caller
        # awaiting the PendingTx would hang forever. Resolve them explicitly so
        # awaiters get a CancelledError instead.
        for pending in pendings:
            if not pending._final_future.done():
                pending._final_future.cancel()
        self._inflight.clear()

    async def stop(self) -> None:
        """Alias for close() — matches the websocket subscriber's lifecycle name."""
        await self.close()

    async def simulate_transaction(
        self,
        type_url: str,
        msgs: list[Any],
    ) -> int:
        """
        Simulate a transaction to estimate gas usage.
        
        This creates a transaction with the user's actual wallet but doesn't sign or broadcast it.
        The simulation happens server-side with empty signatures.
        
        Args:
            type_url: The message type URL (e.g., "/cosmos.bank.v1beta1.MsgSend")
            msgs: List of protobuf messages to include in the transaction
            memo: Optional transaction memo
            
        Returns:
            Estimated gas units required for the transaction (with 20% safety margin)
            
        Raises:
            Exception: If simulation fails or account info cannot be retrieved
        """
        if self.wallet is None:
            raise Exception('No wallet configured. Initialize client with private key or mnemonic.')
        
        logger.debug(f"Simulating transaction for {type_url}")
        
        resp = await self.auth_client.account_info(QueryAccountInfoRequest(address=str(self.wallet.address())))
        if resp.info is None:
            raise Exception('account_info query response is none')
        info = resp.info

        any_messages = [self._create_any_message(msg, type_url) for msg in msgs]

        # Start with the configured default as the lower bound
        base_gas_limit = await self._estimate_gas(type_url)
        current_gas_limit = max(base_gas_limit, 200000)
        # Don't allow runaway retry loops during simulation
        max_simulation_gas = max(int(current_gas_limit * 5), 2_000_000)

        attempt = 0
        while True:
            attempt += 1
            tx = Transaction()
            for msg in any_messages:
                tx.add_message(msg)

            dummy_fee = Coin(amount=1, denom=self.config.fee_denom)

            tx.seal(
                signing_cfgs=[SigningCfg.direct(self.wallet.public_key(), sequence_num=info.sequence)],
                fee=TxFee(amount=[dummy_fee], gas_limit=current_gas_limit, granter=self.fee_granter),
            )

            tx.complete()

            assert tx.tx is not None

            tx_raw = CosmpyTxRaw(
                body_bytes=cast(Message, tx.tx.body).SerializeToString(),
                auth_info_bytes=cast(Message, tx.tx.auth_info).SerializeToString(),
                signatures=[b''],
            )

            tx_bytes = tx_raw.SerializeToString()

            sim_request = SimulateRequest(tx_bytes=tx_bytes)

            try:
                sim_response = await self.tx_client.simulate(sim_request)

                if sim_response is None or sim_response.gas_info is None:
                    raise Exception('Simulation response is None or missing gas_info')

                gas_used = int(sim_response.gas_info.gas_used)
                logger.debug(f"Simulation successful after {attempt} attempt(s): estimated gas = {gas_used}")

                # Add a safety margin to the estimate
                return int(gas_used * self.gas_adjustment)

            except grpc.RpcError as e:
                err = self._exception_from_simulation_error(e)
                if isinstance(err, OutOfGasError) and current_gas_limit < max_simulation_gas:
                    next_limit = min(max_simulation_gas, int(current_gas_limit * 1.5))
                    logger.debug(
                        f"Simulation ran out of gas at {current_gas_limit}, "
                        f"retrying with {next_limit}"
                    )
                    current_gas_limit = next_limit
                    continue

                logger.error(f"Simulation failed: {e.details() if hasattr(e, 'details') else str(e)}")
                raise err

    async def _attempt_submissions(self, pending: PendingTx, gas_limit: Optional[int], account_seq: Optional[int] = None):
        start = datetime.now()

        gas_multiplier = 1.0
        fee_multiplier = self._fee_multipliers[pending.fee_tier]
        current_gas_limit = gas_limit
        next_account_seq = account_seq

        for attempt in range(pending.max_retries + 1):
            try:
                await self._pre_flight_checks()

                pending.attempt = attempt

                gas_multiplier = self._gas_adjustment + (attempt * 0.3)
                tx_hash, used_gas_limit, used_fee, used_sequence = await self._build_and_broadcast(
                    pending.type_url,
                    pending.msgs,
                    current_gas_limit,
                    fee_multiplier,
                    gas_multiplier,
                    next_account_seq,
                )

                # Update known properties
                pending.last_tx_hash = tx_hash
                pending.last_gas_limit = used_gas_limit
                pending.last_fee = used_fee
                current_gas_limit = used_gas_limit
                next_account_seq = used_sequence

                # Await current attempt
                resp = await self.wait_for_tx(tx_hash, timeout=timedelta(seconds=30), poll_period=timedelta(seconds=2))
                assert resp.tx_response is not None
                self._log_tx_response(resp.tx_response)
                next_account_seq = used_sequence + 1
                self._raise_for_status(resp.tx_response)

                logger.debug(f"✅ Transaction included in block!")
                # Success
                pending._final_future.set_result(resp.tx_response)
                return

            except OutOfGasError as oog_err:
                gas_multiplier = self._gas_adjustment + (attempt * 0.3)

                if attempt == pending.max_retries or (pending.timeout and start + pending.timeout < datetime.now()):
                    pending._final_future.set_exception(oog_err)
                    return

                suggested_limit = (
                    int(oog_err.gas_wanted * 1.2) if getattr(oog_err, "gas_wanted", None) else None
                )

                if suggested_limit is None and pending.last_gas_limit is not None:
                    suggested_limit = int(pending.last_gas_limit * 1.3)

                if suggested_limit is None and current_gas_limit is not None:
                    suggested_limit = int(current_gas_limit * 1.3)

                if suggested_limit is None:
                    estimated = await self._estimate_gas(pending.type_url)
                    suggested_limit = int(estimated * 1.5)

                current_gas_limit = suggested_limit
                logger.debug(
                    f"Gas estimation too low, retrying with higher gas limit {current_gas_limit} "
                    f"(attempt {attempt + 2})"
                )
                continue

            except InsufficientFeesError:
                # Invalidate gas price cache - network conditions may have changed
                self._cached_gas_price = None
                self._gas_price_cache_time = None

                fee_multiplier = 1.0 + attempt * 0.5
                if attempt == pending.max_retries or (pending.timeout and start + pending.timeout < datetime.now()):
                    err = InsufficientFeesError("Transaction failed after multiple attempts due to insufficient fees")
                    pending._final_future.set_exception(err)
                    return
                logger.debug("Insufficient fees, retrying with refreshed gas price...")
                continue

            except AccountSequenceMismatchError:
                # Before treating this as a genuine sequence conflict, check
                # whether our own last broadcast actually landed (e.g. a prior
                # timeout-path retry re-broadcast at the same sequence and the
                # original silently landed in between). If it did, resolve via
                # the future instead of resetting the sequence and re-landing.
                if await self._bail_if_landed(pending) is _LandedOutcome.FINALIZED:
                    return

                if pending._same_seq_rebroadcast:
                    # This attempt was a deliberate same-sequence idempotency
                    # re-broadcast (set by the timeout handler), so an ASM
                    # rejection of it is near-certain proof the ORIGINAL tx
                    # landed and consumed the sequence — not a genuine conflict.
                    # Resetting to a fresh sequence here would land the same
                    # operation twice, so first give the indexer a bounded grace
                    # window to catch up and confirm the original hash.
                    pending._same_seq_rebroadcast = False
                    outcome = await self._confirm_landed_with_grace(pending)
                    if outcome is _LandedOutcome.FINALIZED:
                        return
                    if outcome is _LandedOutcome.RETRY_NEW_SEQUENCE:
                        # Landed retryably — its sequence is consumed, so retry
                        # with a fresh one (and better gas if it ran out).
                        next_account_seq = None
                        seeded = self._consume_landed_gas_seed(pending)
                        if seeded is not None:
                            current_gas_limit = seeded
                        # Same retry-or-stop policy as the sibling paths —
                        # _bail_if_landed only returns RETRY_NEW_SEQUENCE with
                        # attempts remaining, but the caller's timeout may have
                        # expired during the grace window.
                        if attempt == pending.max_retries or (pending.timeout and start + pending.timeout < datetime.now()):
                            err = AccountSequenceMismatchError("Transaction failed after multiple attempts due to repeated account sequence mismatches")
                            pending._final_future.set_exception(err)
                            return
                        logger.debug("Account sequence mismatch, retrying...")
                        continue
                    # Still unconfirmed after the grace window — indexer lag
                    # exceeded timeout + grace. Fall through to the
                    # fresh-sequence retry as a last resort; the residual
                    # double-execution risk is pinned by
                    # test_unqueryable_landed_tx_resets_to_fresh_sequence_after_same_seq_rejection.

                # Genuine mismatch (or a landed-retryable tx whose sequence is
                # now consumed): fetch a fresh sequence for the retry.
                next_account_seq = None
                if attempt == pending.max_retries or (pending.timeout and start + pending.timeout < datetime.now()):
                    err = AccountSequenceMismatchError("Transaction failed after multiple attempts due to repeated account sequence mismatches")
                    pending._final_future.set_exception(err)
                    return
                logger.debug("Account sequence mismatch, retrying...")
                if self.account_sequence_retry_delay is not None:
                    delay = self.account_sequence_retry_delay
                    expired = False
                    if pending.timeout:
                        # Never sleep past the deadline — the extra wait would
                        # buy nothing but a failed recheck. Clamping makes the
                        # post-sleep comparison land exactly on the deadline,
                        # so record the overrun here rather than re-deriving it
                        # from a timestamp that may or may not have tipped over.
                        remaining = (start + pending.timeout - datetime.now()).total_seconds()
                        if delay >= remaining:
                            delay, expired = max(0.0, remaining), True
                    await asyncio.sleep(delay)
                    if expired or (pending.timeout and start + pending.timeout < datetime.now()):
                        err = AccountSequenceMismatchError("Transaction deadline exceeded after account sequence retry delay")
                        pending._final_future.set_exception(err)
                        return
                continue

            except TxTimeoutError:
                # wait_for_tx timed out — but the tx may still have landed
                # (slow to index on a load-balanced endpoint). Re-broadcasting a
                # tx that actually landed wastes a fee and is rejected as a
                # duplicate. Guard against that in two ways:
                #   1. Re-query the hash; if it landed, resolve success (or the
                #      landed tx's own failure) via the future — never raise
                #      out of this handler, since _attempt_submissions runs as
                #      a detached task and an escaping exception would leave
                #      pending._final_future unresolved forever.
                outcome = await self._bail_if_landed(pending)
                if outcome is _LandedOutcome.FINALIZED:
                    return
                if outcome is _LandedOutcome.RETRY_NEW_SEQUENCE:
                    # It landed but failed retryably — its sequence is consumed,
                    # so retry with a fresh one instead of the stale sequence
                    # below (which would only seq-mismatch and burn a cycle).
                    next_account_seq = None
                    seeded = self._consume_landed_gas_seed(pending)
                    if seeded is not None:
                        current_gas_limit = seeded
                else:
                    # NOT_LANDED — the retry below keeps the same sequence (a
                    # deliberate same-sequence idempotency re-broadcast). Flag
                    # it so the AccountSequenceMismatchError handler treats a
                    # rejection of this attempt as near-certain proof the
                    # original landed, not a genuine sequence conflict.
                    pending._same_seq_rebroadcast = True
                #   2. Otherwise (not confirmed landed) retry WITHOUT resetting
                #      the account sequence. If the original silently landed
                #      after all, re-broadcasting at the same sequence is
                #      rejected at CheckTx (sequence mismatch, no fee) rather
                #      than acquiring a fresh sequence and landing a second time.
                #      A genuinely-lost tx still re-lands (its sequence was
                #      never consumed).
                #
                #      There is an inherent TOCTOU window: the original tx could
                #      land between the _bail_if_landed check above and this
                #      re-broadcast. That is fine — the same-sequence CheckTx
                #      rejection is the backstop, so this check is a fast-path
                #      optimization, not the correctness guarantee.
                if attempt == pending.max_retries or (pending.timeout and start + pending.timeout < datetime.now()):
                    logger.error("Transaction timed out after multiple attempts")
                    pending._final_future.set_exception(TxTimeoutError())
                    return
                logger.debug(f"Transaction timed out, retrying (attempt {attempt + 2})...")
                continue

            except Exception as err:
                pending._final_future.set_exception(err)
                return

        # Exhausted attempts without setting result
        pending._final_future.set_exception(TxTimeoutError("Transaction failed after maximum retries"))


    async def _build_and_broadcast(
        self,
        type_url: str,
        msgs: list[Any],
        gas_limit: Optional[int],
        fee_multiplier: float,
        gas_multiplier: float,
        account_seq: Optional[int] = None,
    ) -> tuple[str, int, Coin, int]:
        """Build, sign, and SYNC-broadcast the tx; return (hash, gas, fee, seq).

        This is the *broadcast* half only. A non-zero CheckTx code raises the
        classified error here (the tx never entered the mempool), so callers get
        an exception instead of a hash that will never index. Confirmation
        (waiting for the tx to land) is a separate step the caller performs via
        wait_for_tx — broadcast and confirm are already distinct phases.
        """
        any_messages = [ self._create_any_message(msg, type_url) for msg in msgs ]

        tx = Transaction()
        for msg in any_messages:
            tx.add_message(msg)

        if gas_limit is None:
            # Simulation failed upstream — fall back to the static default.
            # The gas_multiplier headroom below applies on this path too, so
            # the first-attempt OOG protection (gas_adjustment) holds even
            # without a simulated estimate.
            gas_limit = await self._estimate_gas(type_url)

        gas_limit = int(gas_limit * gas_multiplier)
        fee = await self._calculate_optimal_fee(gas_limit, fee_multiplier)

        resp = await self.auth_client.account_info(QueryAccountInfoRequest(address=str(self.wallet.address())))
        if resp.info is None:
            raise Exception('account_info query response is none')
        info = resp.info
        resolved_seq = account_seq if account_seq is not None else info.sequence
        logger.debug(f"Account info: seq={resolved_seq}, num={info.account_number}")

        tx.seal(
            signing_cfgs=[ SigningCfg.direct(self.wallet.public_key(), sequence_num=resolved_seq) ],
            fee=TxFee(amount=[ fee ], gas_limit=gas_limit, granter=self.fee_granter),
        )

        tx.sign(
            signer=self.wallet.signer(),
            chain_id=self.config.chain_id,
            account_number=info.account_number,
        )

        tx.complete()
        assert tx.tx is not None

        logger.debug("Broadcasting transaction...")

        # Cast to protobuf Message to satisfy the linter about SerializeToString
        pb_tx = tx.tx  # underlying protobuf message
        from typing import cast
        tx_bytes = cast(Message, pb_tx).SerializeToString()

        req = BroadcastTxRequest(
            tx_bytes=tx_bytes,
            mode=BroadcastMode.SYNC,
        )

        broadcast_result = await self.tx_client.broadcast_tx(req)

        if broadcast_result is None or broadcast_result.tx_response is None:
            raise Exception('broadcast_tx returned None - check network connectivity')

        tx_hash = broadcast_result.tx_response.txhash

        # SYNC broadcast: a non-zero code here is a CheckTx rejection (bad
        # sequence, insufficient fees, ...). The tx was NOT accepted into the
        # mempool and will never be indexed, so raise the classified error now
        # instead of returning a hash that wait_for_tx would only time out on.
        # This is also what makes the idempotency path work: a same-sequence
        # re-broadcast of an already-landed tx is rejected here (sequence
        # mismatch), and because we raise before setting pending.last_tx_hash,
        # the caller's already-landed check still points at the original hash.
        err = self._exception_from_tx_response(broadcast_result.tx_response)
        if err is not None:
            raise err

        logger.debug("⏳ Waiting for transaction to be included in block...")

        return tx_hash, gas_limit, fee, resolved_seq

    async def wait_for_tx(
        self,
        hash: str,
        timeout: Optional[Union[int, float, timedelta]] = None,
        poll_period: Optional[Union[int, float, timedelta]] = None,
    ):
        timeout     = ensure_timedelta(timeout)     if timeout     else timedelta(seconds=self.query_timeout_secs)
        poll_period = ensure_timedelta(poll_period) if poll_period else timedelta(seconds=self.query_interval_secs)

        start = datetime.now()
        while True:
            try:
                return await self._get_tx(hash)
            except TxNotFoundError:
                pass

            delta = datetime.now() - start
            if delta >= timeout:
                raise TxTimeoutError()

            await asyncio.sleep(poll_period.total_seconds())

    async def _get_tx(self, hash: str):
        try:
            resp = await self.tx_client.get_tx(GetTxRequest(hash=hash))
            if resp is None or resp.tx_response is None:
                raise TxNotFoundError()
            return resp
        except grpc.RpcError as e:
            details = e.details()
            if details is not None and "not found" in details:
                raise TxNotFoundError() from e
            raise
        except RuntimeError as e:
            details = str(e)
            if "tx" in details and "not found" in details:
                raise TxNotFoundError() from e
            raise
        except Exception as e:
            details = str(e)
            if "tx" in details and "not found" in details:
                raise TxNotFoundError() from e
            raise


    def _log_tx_response(self, resp: TxResponse):
        logger.debug(f"📋 Transaction Response Details:")
        logger.debug(f"   - Code: {resp.code}")
        logger.debug(f"   - Raw Log: {resp.raw_log}")
        logger.debug(f"   - Tx Hash: {resp.txhash}")
        if hasattr(resp, 'gas_used'):
            logger.debug(f"   - Gas Used: {resp.gas_used}")
        if hasattr(resp, 'gas_wanted'):
            logger.debug(f"   - Gas Wanted: {resp.gas_wanted}")


    def _raise_for_status(self, resp: TxResponse):
        err = self._exception_from_tx_response(resp)
        if err is not None:
            raise err

    async def _try_confirm_landed(self, tx_hash: str) -> Optional[TxResponse]:
        """Re-query a tx by hash. Returns the tx_response if found, None if not
        found or if the query itself failed transiently (e.g. a flaky endpoint
        during a failover). Never raises — callers rely on this to safely
        fall through to normal retry logic instead of hanging or crashing the
        detached submission task.
        """
        try:
            resp = await self._get_tx(tx_hash)
        except Exception as exc:
            # A "not found" here is expected (the tx genuinely didn't land), but
            # a transient RPC error or a real bug looks identical to the caller
            # — log at debug so it's diagnosable without spamming the normal
            # not-yet-indexed case.
            logger.debug("confirm-landed re-query for %s failed: %r", tx_hash, exc)
            return None
        if resp is None or resp.tx_response is None:
            return None
        return resp.tx_response

    async def _bail_if_landed(self, pending: "PendingTx") -> "_LandedOutcome":
        """Check whether pending's last broadcast already landed and decide what
        the retry handler should do:

        - FINALIZED: it landed with a success, or a non-retryable failure, or a
          retryable failure with no retries left — the future is resolved here
          and the caller must return.
        - RETRY_NEW_SEQUENCE: it landed with a *retryable* failure (out-of-gas /
          sequence mismatch / insufficient fees) and retries remain. It consumed
          its on-chain sequence, so the caller must retry with a FRESH sequence
          (reusing the old one would just seq-mismatch and waste a cycle).
        - NOT_LANDED: no last hash, not landed, or the re-query failed — the
          caller falls through to its normal retry path.

        Never raises.
        """
        pending.last_landed_error = None
        if not pending.last_tx_hash:
            return _LandedOutcome.NOT_LANDED
        landed_resp = await self._try_confirm_landed(pending.last_tx_hash)
        if landed_resp is None:
            return _LandedOutcome.NOT_LANDED
        err = self._exception_from_tx_response(landed_resp)
        if (
            isinstance(err, _RETRYABLE_TX_ERRORS)
            and pending.attempt < pending.max_retries
        ):
            pending.last_landed_error = err
            return _LandedOutcome.RETRY_NEW_SEQUENCE
        self._resolve_landed(pending, landed_resp)
        return _LandedOutcome.FINALIZED

    async def _confirm_landed_with_grace(self, pending: "PendingTx", max_attempts: int = 3) -> "_LandedOutcome":
        """Re-check pending's last hash a bounded number of times, giving the
        indexer a grace window (query_interval_secs between checks) to catch up.
        Returns the first non-NOT_LANDED outcome, or NOT_LANDED once the window
        is exhausted. Used when a same-sequence idempotency re-broadcast is
        rejected at CheckTx — near-certain proof the original landed but is not
        yet indexed.
        """
        for _ in range(max_attempts):
            await asyncio.sleep(self.query_interval_secs)
            outcome = await self._bail_if_landed(pending)
            if outcome is not _LandedOutcome.NOT_LANDED:
                return outcome
        return _LandedOutcome.NOT_LANDED

    @staticmethod
    def _consume_landed_gas_seed(pending: "PendingTx") -> Optional[int]:
        """Read-and-clear pending.last_landed_error; if the landed tx ran out
        of gas, return a retry gas limit seeded from its gas_wanted (same bump
        the OutOfGasError handler applies) — reusing the gas limit that just
        proved too small would only fail again. None otherwise.
        """
        landed_err, pending.last_landed_error = pending.last_landed_error, None
        if isinstance(landed_err, OutOfGasError) and landed_err.gas_wanted:
            return int(landed_err.gas_wanted * 1.2)
        return None

    def _resolve_landed(self, pending: "PendingTx", tx_response: TxResponse) -> None:
        """Resolve pending's future for a confirmed-landed tx. If the landed
        tx itself failed on-chain (non-zero code), that error is set on the
        future rather than raised — this may be called from within an except
        handler, and raising here would escape _attempt_submissions (a
        detached asyncio task), leaving the future unresolved forever.
        """
        self._log_tx_response(tx_response)
        try:
            self._raise_for_status(tx_response)
        except Exception as err:
            # Bare Exception is deliberate: this resolves the future for a
            # DETACHED task, so ANY error must land on the future — letting one
            # escape would leave the caller awaiting forever. asyncio.CancelledError
            # and KeyboardInterrupt are BaseException (py>=3.10), so cancellation
            # still propagates correctly and is not swallowed here.
            pending._final_future.set_exception(err)
            return
        pending._final_future.set_result(tx_response)

    def _classify_error_from_message(self, error_msg: str) -> type[Exception]:
        """
        Classify error type based on error message content.

        Args:
            error_msg: Error message string to classify

        Returns:
            Exception class that best matches the error message
        """
        error_lower = error_msg.lower()

        if "out of gas" in error_lower:
            return OutOfGasError
        elif "account sequence mismatch" in error_lower:
            return AccountSequenceMismatchError
        elif "insufficient fees" in error_lower:
            return InsufficientFeesError
        else:
            return TxError

    def _exception_from_tx_response(self, resp: TxResponse):
        if resp.code == 0:
            return None

        error_class = self._classify_error_from_message(resp.raw_log)

        if error_class == OutOfGasError:
            gas_wanted = getattr(resp, "gas_wanted", None)
            gas_used = getattr(resp, "gas_used", None)
            return OutOfGasError(
                f"Transaction ran out of gas: {resp.raw_log}",
                gas_wanted=int(gas_wanted) if gas_wanted else None,
                gas_used=int(gas_used) if gas_used else None,
            )
        elif error_class == AccountSequenceMismatchError:
            return AccountSequenceMismatchError(f"Sequence mismatch: {resp.raw_log}")
        elif error_class == InsufficientFeesError:
            return InsufficientFeesError(f"insufficient fees{self._fee_granter_hint()}: {resp.raw_log}")
        else:
            return TxError(
                codespace=resp.codespace,
                code=resp.code,
                message=resp.raw_log,
                tx_hash=resp.txhash
            )

    def _fee_granter_hint(self) -> str:
        """Name the granter in fee errors when one is configured.

        A revoked or missing fee grant surfaces as a plain insufficient-fee
        error against a signer that was never meant to hold funds, which reads
        as "the wallet is broke" and sends debugging the wrong way. The
        allowance itself is not queried here: the feegrant module has no REST
        wrapper generated, so a check would silently only work in gRPC mode.
        """
        if self.fee_granter is None:
            return ""
        return (
            f" (fee granter {self.fee_granter} is configured — verify the grant to "
            f"{self.wallet.address()} still exists and its spend limit is not exhausted)"
        )

    def _exception_from_simulation_error(self, error: grpc.RpcError) -> Exception:
        """
        Parse gRPC error from simulation and return appropriate exception.

        Applies same error classification as _exception_from_tx_response
        for consistency between simulation and actual transaction errors.

        Args:
            error: gRPC error from simulation call

        Returns:
            Appropriate exception type based on error details
        """
        error_details = error.details() if hasattr(error, 'details') else str(error)
        error_msg = error_details or str(error)
        error_class = self._classify_error_from_message(error_msg)

        if error_class == OutOfGasError:
            return OutOfGasError(f"Simulation ran out of gas: {error_msg}")
        elif error_class == AccountSequenceMismatchError:
            return AccountSequenceMismatchError(f"Sequence mismatch during simulation: {error_msg}")
        elif error_class == InsufficientFeesError:
            return InsufficientFeesError(f"Insufficient fees during simulation{self._fee_granter_hint()}: {error_msg}")
        else:
            code = error.code() if hasattr(error, 'code') else None
            try:
                code_value = code.value[0] if isinstance(code.value, tuple) else int(code.value)
            except (TypeError, IndexError, AttributeError):
                code_value = 1

            return TxError(
                codespace="simulation",
                code=code_value,
                message=error_msg,
                tx_hash=None
            )

    async def _estimate_gas(self, type_url: str) -> int:
        if self.base_gas is not None:
            return int(self.base_gas * self.gas_adjustment)

        base_gas = self._default_gas_limits.get(type_url, 200000)

        # Add safety margin
        return int(base_gas * self.gas_adjustment)


    async def _get_current_gas_price(self) -> float:
        """
        Get current gas price with caching.

        Queries feemarket module for dynamic gas price, falling back to static
        config price on failure. Caches result for configured TTL.

        Returns:
            Gas price in base units per gas unit
        """
        # Check cache validity
        if self._cached_gas_price is not None and self._gas_price_cache_time is not None:
            age = datetime.now() - self._gas_price_cache_time
            if age.total_seconds() < self._gas_price_cache_ttl_secs:
                return float(self._cached_gas_price)

        # Try dynamic price if enabled
        if self.config.use_dynamic_gas_price and self.feemarket_client is not None:
            try:
                response = await self.feemarket_client.gas_price(
                    GasPriceRequest(denom=self.config.fee_denom)
                )
                if response.price is not None:
                    # DecCoin.amount is a string decimal with 18 decimal places (cosmos.Dec format)
                    # e.g., "10000000000000000000" represents 10.0
                    price_raw = Decimal(response.price.amount)
                    price = price_raw / Decimal(10 ** 18)
                    adjusted_price = price * Decimal(str(self.config.dynamic_gas_price_default_multiplier))
                    self._cached_gas_price = adjusted_price
                    self._gas_price_cache_time = datetime.now()
                    logger.debug(f"Using dynamic gas price: {adjusted_price} {self.config.fee_denom}/gas")
                    # Feemarket returns the minimum acceptable price; apply configured buffer
                    # to avoid "insufficient fees" rejections from validators with higher minimums.
                    return float(adjusted_price)
            except Exception as e:
                logger.debug(f"Failed to query dynamic gas price, using static: {e}")

        # Fall back to static config price
        return self.config.fee_minimum_gas_price


    async def _check_network_congestion(self) -> Optional[float]:
        """
        Check network congestion via feemarket state.

        Returns:
            Congestion multiplier (1.0 = normal, >1.0 = congested) or None if check failed
        """
        if not self.config.congestion_aware_fees or self.feemarket_client is None:
            return None

        try:
            state_resp = await self.feemarket_client.state(StateRequest())
            if state_resp.state is None:
                return None

            state = state_resp.state

            # Calculate average utilization in window
            if not state.window:
                return None

            params_resp = await self.feemarket_client.params(ParamsRequest())
            if params_resp.params is None:
                return None

            avg_utilization = sum(state.window) / len(state.window)
            max_utilization = params_resp.params.max_block_utilization

            if max_utilization == 0:
                return None

            utilization_ratio = avg_utilization / max_utilization

            # Suggest multiplier based on utilization
            # >80% = congested, suggest 1.5x
            # >90% = very congested, suggest 2.0x
            if utilization_ratio > 0.9:
                logger.debug(f"High network congestion detected: {utilization_ratio:.1%}")
                return 2.0
            elif utilization_ratio > 0.8:
                logger.debug(f"Moderate network congestion detected: {utilization_ratio:.1%}")
                return 1.5

            return 1.0

        except Exception as e:
            logger.debug(f"Failed to check network congestion: {e}")
            return None


    async def _calculate_optimal_fee(self, gas_limit: int, fee_multiplier: float) -> Coin:
        """
        Calculate optimal transaction fee.

        Uses dynamic gas prices from feemarket when available, falling back to
        static config prices. Applies fee tier multiplier and optional congestion
        multiplier.

        Args:
            gas_limit: Gas limit for the transaction
            fee_multiplier: Base fee tier multiplier (from retry logic)

        Returns:
            Coin object with calculated fee amount
        """
        # Get current gas price (cached or fresh)
        base_price = await self._get_current_gas_price()

        # Apply fee tier multiplier
        price_with_tier = base_price * fee_multiplier

        # Apply congestion multiplier if enabled
        congestion_multiplier = await self._check_network_congestion()
        if congestion_multiplier is not None and congestion_multiplier > 1.0:
            price_with_tier *= congestion_multiplier
            logger.debug(f"Applied congestion multiplier: {congestion_multiplier}x")

        fee_amount = int(gas_limit * price_with_tier)
        if self.max_fees is not None and fee_amount > self.max_fees:
            raise MaxFeesExceededError(
                f"Computed fee {fee_amount}{self.config.fee_denom} exceeds max_fees cap "
                f"{self.max_fees}{self.config.fee_denom} (gas_limit={gas_limit})"
            )
        return Coin(amount=fee_amount, denom=self.config.fee_denom)


    async def _pre_flight_checks(self):
        if not self.wallet:
            raise Exception("No wallet configured")

        try:
            # Check if account exists
            _ = await self.auth_client.account(QueryAccountRequest(address=str(self.wallet.address())))

            # Check balance (estimate worst-case fee for checks)
            estimated_fee = int(300000 * self.config.fee_minimum_gas_price * self._fee_multipliers[FeeTier.PRIORITY])

            if self.fee_granter is not None:
                # The granter pays the fees, so never reject on the signer's balance.
                # A low/unreadable granter balance is only warned about - the fee
                # grant is on chain and the broadcast itself is the real check.
                resp = await self.bank_client.balance(QueryBalanceRequest(address=str(self.fee_granter), denom=self.config.fee_denom))
                if resp is not None and resp.balance is not None and int(resp.balance.amount) < estimated_fee:
                    logger.warning(
                        f"Fee granter {self.fee_granter} balance {resp.balance.amount} {self.config.fee_denom} "
                        f"is below the estimated fee {estimated_fee} {self.config.fee_denom}"
                    )
                # The signer's own balance is not a gate here, but it is the
                # first thing anyone looks at in the field, so record it.
                signer_resp = await self.bank_client.balance(QueryBalanceRequest(address=str(self.wallet.address()), denom=self.config.fee_denom))
                if signer_resp is not None and signer_resp.balance is not None:
                    logger.debug(
                        f"Fees are paid by granter {self.fee_granter}; signer {self.wallet.address()} "
                        f"holds {signer_resp.balance.amount} {self.config.fee_denom}"
                    )
                return

            resp = await self.bank_client.balance(QueryBalanceRequest(address=str(self.wallet.address()), denom=self.config.fee_denom))
            if resp is not None and resp.balance is not None:
                if int(resp.balance.amount) < estimated_fee:
                    raise InsufficientBalanceError(
                        f"Insufficient balance: need at least {estimated_fee} {self.config.fee_denom}, "
                        f"have {resp.balance}. Please fund your wallet."
                    )

                # Warn if balance is getting low
                if int(resp.balance.amount) < estimated_fee * 5:
                    logger.debug(f"⚠️ Low balance warning: {resp.balance} {self.config.fee_denom} remaining")

        except InsufficientBalanceError:
            raise
        except Exception as e:
            logger.debug(f"Pre-flight check warning: {e}")


    def _create_any_message(self, message, type_url: str):
        """
        Convert a betterproto2 message to a format cosmpy can handle without double-wrapping.
        This exists because we're still using cosmpy's transaction signing and serialization,
        and therefore their protobufs, which are not betterproto2 protobufs.  The underlying
        wire protocol is compatible, but the libraries/interfaces are not.
        """
        logger.debug(f"Creating message wrapper for type_url: {type_url}")
        
        class BetterprotoWrapper:
            def __init__(self, betterproto_message, type_url: str):
                self._message_bytes = bytes(betterproto_message)
                self._type_url = type_url
                logger.debug(f"Wrapper created with {len(self._message_bytes)} bytes")
                
            def SerializeToString(self, deterministic: bool = False) -> bytes:
                """Return the serialized betterproto message bytes."""
                # Note: betterproto serialization is already deterministic, 
                # so we can ignore the deterministic parameter
                return self._message_bytes
                
            @property
            def DESCRIPTOR(self):
                """Mock descriptor that cosmpy uses to determine type URL."""
                class MockDescriptor:
                    def __init__(self, type_url: str):
                        # Remove leading slash for full_name format
                        self.full_name = type_url.lstrip('/')
                        
                return MockDescriptor(self._type_url)
        
        wrapped_message = BetterprotoWrapper(message, type_url)
        return wrapped_message
