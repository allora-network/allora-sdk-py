import asyncio
from enum import Enum
from random import paretovariate
import time
import grpc
from datetime import datetime, timedelta
import logging
import traceback
from typing import Any, Optional, Union, Dict
from google.protobuf.message import Message
import uuid

from cosmpy.aerial.wallet import LocalWallet
from cosmpy.aerial.tx import SigningCfg, Transaction, TxFee
from cosmpy.aerial.coins import Coin
from cosmpy.aerial.client.utils import ensure_timedelta

from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.protos.cosmos.auth.v1beta1 import QueryAccountInfoRequest, QueryAccountRequest
from allora_sdk.protos.cosmos.bank.v1beta1 import QueryBalanceRequest
from allora_sdk.protos.cosmos.base.abci.v1beta1 import TxResponse
from allora_sdk.protos.cosmos.tx.v1beta1 import BroadcastMode, BroadcastTxRequest, GetTxRequest
from allora_sdk.rest.cosmos_auth_v1beta1_rest_client import CosmosAuthV1Beta1QueryLike
from allora_sdk.rest.cosmos_bank_v1beta1_rest_client import CosmosBankV1Beta1QueryLike
from allora_sdk.rest.cosmos_tx_v1beta1_rest_client import CosmosTxV1Beta1ServiceLike


logger = logging.getLogger("allora_sdk")

class PendingTx:
    def __init__(
        self,
        manager: "TxManager",
        *,
        parent_tx_id: int,
        type_url: str,
        msg: Any,
        fee_tier: "FeeTier",
        max_retries: int,
        timeout: Optional[timedelta],
    ):
        self.manager = manager
        self.parent_tx_id = parent_tx_id
        self.created_at = datetime.now()
        self.type_url = type_url
        self.msg = msg
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
    def __init__(self, codespace: str, code: int, message: str, tx_hash: str):
        self.codespace = codespace
        self.code = code
        self.message = message
        self.tx_hash = tx_hash

    def __str__(self):
        return f"TxError: codespace={self.codespace} code={self.code} tx_hash={self.tx_hash} {self.message}"

class InsufficientBalanceError(Exception):
    """Raised when account doesn't have enough balance for fees."""
    pass

class OutOfGasError(Exception):
    """Raised when transaction runs out of gas."""
    pass

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
        config: AlloraNetworkConfig,
        query_interval_secs: int = 2,
        query_timeout_secs: int = 5,
    ):
        self.wallet = wallet
        self.tx_client = tx_client
        self.auth_client = auth_client
        self.bank_client = bank_client
        self.config = config
        self.query_interval_secs = query_interval_secs
        self.query_timeout_secs = query_timeout_secs
        self.parent_tx_id = 0

        self._default_gas_limits = {
            "/emissions.v9.InsertWorkerPayloadRequest": 250000,
            "/cosmos.bank.v1beta1.MsgSend": 50000,
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

    async def submit_transaction(
        self,
        type_url: str,
        msg: Any,
        gas_limit: Optional[int] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
        max_retries: int = 2,
        timeout: Optional[timedelta] = None,
    ):
        if self.wallet is None:
            raise Exception('No wallet configured. Initialize client with private key or mnemonic.')

        pending = PendingTx(
            manager=self,
            parent_tx_id=self.parent_tx_id,
            type_url=type_url,
            msg=msg,
            fee_tier=fee_tier,
            max_retries=max_retries,
            timeout=timeout,
        )
        self.parent_tx_id += 1

        # Kick off processing as a background task; caller can await the PendingTx
        asyncio.create_task(self._attempt_submissions(pending, gas_limit))

        return pending

    async def _attempt_submissions(self, pending: PendingTx, gas_limit: Optional[int]):
        start = datetime.now()

        gas_multiplier = 1.0
        fee_multiplier = self._fee_multipliers[pending.fee_tier]

        for attempt in range(pending.max_retries + 1):
            try:
                await self._pre_flight_checks()

                pending.attempt = attempt

                gas_multiplier = 1.0 + (attempt * 0.3)
                tx_hash, used_gas_limit, used_fee = await self._build_and_broadcast(
                    pending.type_url,
                    pending.msg,
                    gas_limit,
                    fee_multiplier,
                    gas_multiplier,
                )

                # Update known properties
                pending.last_tx_hash = tx_hash
                pending.last_gas_limit = used_gas_limit
                pending.last_fee = used_fee

                # Await current attempt
                resp = await self.wait_for_tx(tx_hash, timeout=timedelta(seconds=30), poll_period=timedelta(seconds=2))
                assert resp.tx_response is not None
                self._log_tx_response(resp.tx_response)
                self._raise_for_status(resp.tx_response)

                logger.debug(f"✅ Transaction included in block!")
                # Success
                pending._final_future.set_result(resp.tx_response)
                return

            except OutOfGasError:
                gas_multiplier = 1.0 + (attempt * 0.3)
                if attempt == pending.max_retries or (pending.timeout and start + pending.timeout < datetime.now()):
                    raise Exception("Transaction failed after multiple attempts due to insufficient gas")
                logger.debug(f"Gas estimation too low, retrying with higher gas (attempt {attempt + 2})")
                continue

            except InsufficientFeesError:
                fee_multiplier = 1.0 + attempt * 0.5
                if attempt == pending.max_retries or (pending.timeout and start + pending.timeout < datetime.now()):
                    raise Exception("Transaction failed after multiple attempts due to insufficient fees")
                logger.debug("Insufficient fees, retrying...")
                continue

            except AccountSequenceMismatchError:
                # Account sequence will be recalculated on next attempt
                # TODO: maybe build a nonce manager?
                if attempt == pending.max_retries or (pending.timeout and start + pending.timeout < datetime.now()):
                    raise Exception("Transaction failed after multiple attempts due to repeated account sequence mismatches")
                logger.debug("Account sequence mismatch, retrying...")
                continue

            except TxTimeoutError:
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
        msg: Any,
        gas_limit: Optional[int],
        fee_multiplier: float,
        gas_multiplier: float,
    ) -> tuple[str, int, Coin]:
        any_message = self._create_any_message(msg, type_url)

        tx = Transaction()
        tx.add_message(any_message)

        if gas_limit is None:
            gas_limit = await self._estimate_gas(type_url)

        gas_limit = int(gas_limit * gas_multiplier)
        fee = await self._calculate_optimal_fee(gas_limit, fee_multiplier)

        resp = self.auth_client.account_info(QueryAccountInfoRequest(address=str(self.wallet.address())))
        if resp.info is None:
            raise Exception('account_info query response is none')
        info = resp.info
        logger.debug(f"Account info: seq={info.sequence}, num={info.account_number}")

        tx.seal(
            signing_cfgs=[ SigningCfg.direct(self.wallet.public_key(), sequence_num=info.sequence) ],
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

        broadcast_result = self.tx_client.broadcast_tx(req)

        if broadcast_result is None or broadcast_result.tx_response is None:
            raise Exception('broadcast_tx returned None - check network connectivity')

        tx_hash = broadcast_result.tx_response.txhash
        logger.debug("⏳ Waiting for transaction to be included in block...")

        return tx_hash, gas_limit, fee

    async def wait_for_tx(self,
        hash: str,
        timeout: Optional[Union[int, float, timedelta]] = None,
        poll_period: Optional[Union[int, float, timedelta]] = None,
    ):
        timeout     = ensure_timedelta(timeout)     if timeout     else timedelta(seconds=self.query_timeout_secs)
        poll_period = ensure_timedelta(poll_period) if poll_period else timedelta(seconds=self.query_interval_secs)

        start = datetime.now()
        while True:
            try:
                return self._get_tx(hash)
            except TxNotFoundError:
                pass

            delta = datetime.now() - start
            if delta >= timeout:
                raise TxTimeoutError()

            await asyncio.sleep(poll_period.total_seconds())

    def _get_tx(self, hash: str):
        try:
            resp = self.tx_client.get_tx(GetTxRequest(hash=hash))
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

    def _exception_from_tx_response(self, resp: TxResponse):
        if resp.code == 0:
            return None

        if "out of gas" in resp.raw_log.lower():
            return OutOfGasError(f"Transaction ran out of gas: {resp.raw_log}")
        elif "account sequence mismatch" in resp.raw_log.lower():
            return AccountSequenceMismatchError(f"Sequence mismatch: {resp.raw_log}")
        elif "insufficient fees" in resp.raw_log.lower():
            return InsufficientFeesError("insufficient fees")
        else:
            return TxError(
                codespace=resp.codespace,
                code=resp.code,
                message=resp.raw_log,
                tx_hash=resp.txhash
            )

    async def _estimate_gas(self, type_url: str) -> int:
        # TODO
        base_gas = self._default_gas_limits.get(type_url, 200000)

        # Add 20% safety margin
        return int(base_gas * 1.2)


    async def _calculate_optimal_fee(self, gas_limit: int, fee_multiplier: float) -> Coin:
        base_price = self.config.fee_minimum_gas_price
        fee_amount = int(gas_limit * base_price * fee_multiplier)
        return Coin(amount=fee_amount, denom=self.config.fee_denom)


    async def _pre_flight_checks(self):
        if not self.wallet:
            raise Exception("No wallet configured")

        try:
            # Check if account exists
            _ = self.auth_client.account(QueryAccountRequest(address=str(self.wallet.address())))

            # Check balance (estimate worst-case fee for checks)
            resp = self.bank_client.balance(QueryBalanceRequest(address=str(self.wallet.address()), denom=self.config.fee_denom))
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

