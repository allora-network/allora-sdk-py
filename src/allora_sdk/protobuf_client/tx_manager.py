from enum import Enum
import logging
from typing import Any, Optional
from cosmpy.aerial.client import LedgerClient, TxResponse
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.aerial.tx import SigningCfg, Transaction, TxFee
from cosmpy.aerial.coins import Coin
from google.protobuf.any_pb2 import Any as ProtobufAny

from allora_sdk.protobuf_client.config import AlloraNetworkConfig

logger = logging.getLogger(__name__)

# Fee tier options for user-friendly transaction submission
class FeeTier(Enum):
    ECO      = "eco"           # Minimum fees, slower confirmation
    STANDARD = "standard" # Balanced fees and speed
    PRIORITY = "priority" # Higher fees, faster confirmation

# Custom exceptions for better error handling
class TxError(Exception):
    """Base exception for transaction errors."""
    pass

class InsufficientBalanceError(TxError):
    """Raised when account doesn't have enough balance for fees."""
    pass

class OutOfGasError(TxError):
    """Raised when transaction runs out of gas."""
    pass

class AccountSequenceMismatchError(TxError):
    """Raised when account sequence is out of sync."""
    pass

class TxManager:
    """Enhanced transaction manager with intelligent gas/fee handling."""

    def __init__(
        self,
        wallet: LocalWallet,
        client: LedgerClient,
        config: AlloraNetworkConfig,
    ):
        self.wallet = wallet
        self.client = client
        self.config = config

        # Default gas limits for different message types
        self._default_gas_limits = {
            "/emissions.v9.InsertWorkerPayloadRequest": 250000,
            "/cosmos.bank.v1beta1.MsgSend": 50000,
            "/cosmos.staking.v1beta1.MsgDelegate": 100000,
            "/cosmos.staking.v1beta1.MsgUndelegate": 100000,
        }

        # Fee tier multipliers
        self._fee_multipliers = {
            FeeTier.ECO: 1.0,        # Minimum fees
            FeeTier.STANDARD: 1.5,   # 50% higher than minimum
            FeeTier.PRIORITY: 2.5,   # 150% higher than minimum
        }

    async def submit_transaction(
        self,
        type_url: str,
        msg: Any,
        gas_limit: Optional[int] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
        max_retries: int = 2
    ) -> TxResponse:
        """Submit transaction with intelligent gas/fee handling."""

        if self.wallet is None:
            raise TxError('No wallet configured. Initialize client with private key or mnemonic.')

        # Pre-flight checks
        await self._pre_flight_checks()

        # Try submission with retries
        for attempt in range(max_retries + 1):
            try:
                gas_multiplier = 1.0 + (attempt * 0.3)  # Increase gas 30% each retry
                return await self._submit_single_attempt(type_url, msg, gas_limit, fee_tier, gas_multiplier)
            except OutOfGasError:
                if attempt == max_retries:
                    raise TxError("Transaction failed after multiple attempts due to insufficient gas")
                logger.warning(f"Gas estimation too low, retrying with higher gas (attempt {attempt + 2})")
                continue
            except AccountSequenceMismatchError:
                if attempt == max_retries:
                    raise
                logger.warning("Account sequence mismatch, retrying...")
                continue
            except Exception as e:
                # Don't retry on other types of errors
                raise TxError(f"Transaction failed: {str(e)}")

        raise TxError("Transaction failed after maximum retries")

    async def _submit_single_attempt(
        self,
        type_url: str,
        msg: Any,
        gas_limit: Optional[int],
        fee_tier: FeeTier,
        gas_multiplier: float
    ) -> TxResponse:
        """Submit a single transaction attempt."""

        # Convert to Any message for cosmpy
        any_message = self._create_any_message(msg, type_url)

        # Create transaction
        tx = Transaction()
        tx.add_message(any_message)

        # Estimate gas if not provided
        if gas_limit is None:
            gas_limit = await self._estimate_gas(type_url)

        # Apply gas multiplier for retries
        gas_limit = int(gas_limit * gas_multiplier)

        # Calculate optimal fee
        fee = await self._calculate_optimal_fee(gas_limit, fee_tier)

        try:
            # Get fresh account info for each attempt
            account_info = self.client.query_account(self.wallet.address())

            tx.seal(
                signing_cfgs=[SigningCfg.direct(self.wallet.public_key(), sequence_num=account_info.sequence)],
                fee=TxFee(amount=fee, gas_limit=gas_limit).to_proto(),
            )
            tx.sign(
                signer=self.wallet.signer(),
                chain_id=self.config.chain_id,
                account_number=account_info.number,
            )

            tx_response = self.client.broadcast_tx(tx)
            if tx_response is None or tx_response.response is None:
                raise TxError('Transaction response is None')

            response = tx_response.response

            if response.code != 0:
                # Parse common error types
                if "out of gas" in response.raw_log.lower():
                    raise OutOfGasError(f"Transaction ran out of gas: {response.raw_log}")
                elif "account sequence mismatch" in response.raw_log.lower():
                    raise AccountSequenceMismatchError(f"Sequence mismatch: {response.raw_log}")
                else:
                    raise TxError(f"Transaction failed (code {response.code}): {response.raw_log}")

            logger.info(f"✅ Transaction successful: {response.hash} (gas used: {response.gas_used})")
            return response

        except (OutOfGasError, AccountSequenceMismatchError):
            # Re-raise these for retry logic
            raise
        except Exception as e:
            logger.error(f"Transaction submission error: {e}")
            raise TxError(f"Failed to submit transaction: {str(e)}")

    async def _estimate_gas(self, type_url: str) -> int:
        """Estimate gas with safety margin based on message type."""
        base_gas = self._default_gas_limits.get(type_url, 200000)

        # Add 20% safety margin
        return int(base_gas * 1.2)

    async def _calculate_optimal_fee(self, gas_limit: int, fee_tier: FeeTier) -> Coin:
        """Calculate fee based on tier and network conditions."""
        base_price = self.config.fee_minimum_gas_price
        multiplier = self._fee_multipliers[fee_tier]

        fee_amount = int(gas_limit * base_price * multiplier)

        return Coin(amount=fee_amount, denom=self.config.fee_denom)

    async def _pre_flight_checks(self):
        """Check account balance and other prerequisites."""
        if not self.wallet:
            raise TxError("No wallet configured")

        try:
            # Check if account exists
            account_info = self.client.query_account(self.wallet.address())

            # Check balance (estimate worst-case fee for checks)
            balance = self.client.query_bank_balance(self.wallet.address(), self.config.fee_denom)
            estimated_fee = int(300000 * self.config.fee_minimum_gas_price * self._fee_multipliers[FeeTier.PRIORITY])

            if balance < estimated_fee:
                raise InsufficientBalanceError(
                    f"Insufficient balance: need at least {estimated_fee} {self.config.fee_denom}, "
                    f"have {balance}. Please fund your wallet."
                )

            # Warn if balance is getting low
            if balance < estimated_fee * 5:
                logger.warning(f"⚠️ Low balance warning: {balance} {self.config.fee_denom} remaining")

        except InsufficientBalanceError:
            raise
        except Exception as e:
            logger.warning(f"Pre-flight check warning: {e}")
            # Don't fail on pre-flight check errors, just warn


    def _create_any_message(self, message, type_url: str) -> ProtobufAny:
        """
        Convert a betterproto message to a protobuf Any message for cosmpy.

        Args:
            message: Betterproto message instance
            type_url: The type URL for the message

        Returns:
            ProtobufAny message that cosmpy can handle
        """
        # Serialize the betterproto message to bytes
        message_bytes = bytes(message)

        # Create Any message
        any_message = ProtobufAny()
        any_message.type_url = type_url
        any_message.value = message_bytes

        return any_message
