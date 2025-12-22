import asyncio
from enum import Enum
import traceback
import grpc
from datetime import datetime, timedelta
from decimal import Decimal
import logging
from typing import Any, Optional, Union, Dict, cast
from google.protobuf.message import Message

from cosmpy.aerial.wallet import LocalWallet
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
        self.start = datetime.now()
        self.timeout = timeout
        self.attempt: int = 0

        # Final outcome future: resolves to TxResponse or raises
        self._final_future: asyncio.Future[TxResponse] = asyncio.get_running_loop().create_future()

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

class TxNotFoundError(Exception):
    pass

class TxTimeoutError(Exception):
    pass


class TxManager:
    def __init__(
        self,
        wallet: LocalWallet,
        tx_client: CosmosTxV1Beta1ServiceLike,
        auth_client: CosmosAuthV1Beta1QueryLike,
        bank_client: CosmosBankV1Beta1QueryLike,
        feemarket_client: Optional[FeemarketFeemarketV1QueryLike],
        config: AlloraNetworkConfig,
        query_interval_secs: int = 2,
        query_timeout_secs: int = 5,
    ):
        self.wallet = wallet
        self.tx_client = tx_client
        self.auth_client = auth_client
        self.bank_client = bank_client
        self.feemarket_client = feemarket_client
        self.config = config
        self.query_interval_secs = query_interval_secs
        self.query_timeout_secs = query_timeout_secs
        self.parent_tx_id = 0

        self._default_gas_limits = {
            "/emissions.v9.InsertWorkerPayloadRequest": 250000,
            "/emissions.v9.CreateNewTopicRequest": 300000,
            "/emissions.v9.FundTopicRequest": 150000,
            "/emissions.v9.BulkAddToTopicWorkerWhitelistRequest": 200000,
            "/emissions.v9.BulkAddToTopicReputerWhitelistRequest": 200000,
            "/emissions.v9.RewardDelegateStakeRequest": 150000,
            "/cosmos.bank.v1beta1.MsgSend": 250000,
            "/cosmos.staking.v1beta1.MsgDelegate": 100000,
            "/cosmos.staking.v1beta1.MsgUndelegate": 100000,
        }

        self._fee_multipliers = {
            FeeTier.ECO: 1.0,        # Minimum fees
            FeeTier.STANDARD: 1.5,   # 50% higher than minimum
            FeeTier.PRIORITY: 2.5,   # 150% higher than minimum
        }

        # Pending tx hash watchers monitored by a background task
        self._pending_attempts: Dict[str, Dict[str, Any]] = {}
        self._monitor_task: Optional[asyncio.Task] = None
        self._monitor_cond = asyncio.Condition()

        # Gas price caching
        self._cached_gas_price: Optional[Decimal] = None
        self._gas_price_cache_time: Optional[datetime] = None
        self._gas_price_cache_ttl_secs: int = config.gas_price_cache_ttl_secs

    async def submit_transaction(
        self,
        type_url: str,
        msgs: list[Any],
        gas_limit: Optional[int] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
        max_retries: int = 2,
        timeout: Optional[timedelta] = None,
        account_seq: Optional[int] = None,
    ):
        if self.wallet is None:
            raise Exception('No wallet configured. Initialize client with private key or mnemonic.')

        estimated_gas_limit = gas_limit
        if estimated_gas_limit is None:
            try:
                estimated_gas_limit = await self.simulate_transaction(type_url, msgs)
                logger.debug(f"Simulated gas requirement for {type_url}: {estimated_gas_limit}")
                fee_preview = await self._calculate_optimal_fee(
                    estimated_gas_limit,
                    self._fee_multipliers[fee_tier],
                )
                logger.debug(f"Estimated fee for {type_url}: {fee_preview.amount} {fee_preview.denom}")
            except Exception as e:
                logger.debug(f"Unable to simulate transaction for gas estimate, falling back to defaults: {e}")
                estimated_gas_limit = None

        pending = PendingTx(
            manager=self,
            parent_tx_id=self.parent_tx_id,
            type_url=type_url,
            msgs=msgs,
            fee_tier=fee_tier,
            max_retries=max_retries,
            timeout=timeout,
        )
        self.parent_tx_id += 1

        # Kick off processing as a background task; caller can await the PendingTx
        asyncio.create_task(
            self._attempt_submissions(pending, estimated_gas_limit, account_seq=account_seq)
        )

        return pending
    
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
                fee=TxFee(amount=[dummy_fee], gas_limit=current_gas_limit),
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

                # Add a 20% safety margin to the estimate
                return int(gas_used * 1.2)

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

                gas_multiplier = 1.0 + (attempt * 0.3)
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
                gas_multiplier = 1.0 + (attempt * 0.3)

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
                    raise Exception("Transaction failed after multiple attempts due to insufficient fees")
                logger.debug("Insufficient fees, retrying with refreshed gas price...")
                continue

            except AccountSequenceMismatchError:
                # Account sequence will be recalculated on next attempt
                # TODO: maybe build a nonce manager?
                next_account_seq = None
                if attempt == pending.max_retries or (pending.timeout and start + pending.timeout < datetime.now()):
                    raise Exception("Transaction failed after multiple attempts due to repeated account sequence mismatches")
                logger.debug("Account sequence mismatch, retrying...")
                continue

            except TxTimeoutError:
                next_account_seq = None
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
        any_messages = [ self._create_any_message(msg, type_url) for msg in msgs ]

        tx = Transaction()
        for msg in any_messages:
            tx.add_message(msg)

        if gas_limit is None:
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
            fee=TxFee(amount=[ fee ], gas_limit=gas_limit),
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
            return InsufficientFeesError("insufficient fees")
        else:
            return TxError(
                codespace=resp.codespace,
                code=resp.code,
                message=resp.raw_log,
                tx_hash=resp.txhash
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
            return InsufficientFeesError(f"Insufficient fees during simulation: {error_msg}")
        else:
            code = error.code() if hasattr(error, 'code') else None
            code_value = code.value[0] if code else 1

            return TxError(
                codespace="simulation",
                code=code_value,
                message=error_msg,
                tx_hash=None
            )

    async def _estimate_gas(self, type_url: str) -> int:
        # TODO
        base_gas = self._default_gas_limits.get(type_url, 200000)

        # Add 20% safety margin
        return int(base_gas * 1.2)


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
                    self._cached_gas_price = price
                    self._gas_price_cache_time = datetime.now()
                    logger.debug(f"Using dynamic gas price: {price} {self.config.fee_denom}/gas")
                    return float(price) * 10
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
        return Coin(amount=fee_amount, denom=self.config.fee_denom)


    async def _pre_flight_checks(self):
        if not self.wallet:
            raise Exception("No wallet configured")

        try:
            # Check if account exists
            _ = await self.auth_client.account(QueryAccountRequest(address=str(self.wallet.address())))

            # Check balance (estimate worst-case fee for checks)
            resp = await self.bank_client.balance(QueryBalanceRequest(address=str(self.wallet.address()), denom=self.config.fee_denom))
            if resp is not None and resp.balance is not None:
                estimated_fee = int(300000 * self.config.fee_minimum_gas_price * self._fee_multipliers[FeeTier.PRIORITY])

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
