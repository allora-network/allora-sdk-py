from enum import Enum
import logging
import traceback
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

    def __init__(
        self,
        wallet: LocalWallet,
        client: LedgerClient,
        config: AlloraNetworkConfig,
    ):
        self.wallet = wallet
        self.client = client
        self.config = config

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

    async def submit_transaction(
        self,
        type_url: str,
        msg: Any,
        gas_limit: Optional[int] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
        max_retries: int = 2
    ) -> TxResponse:
        if self.wallet is None:
            raise TxError('No wallet configured. Initialize client with private key or mnemonic.')

        await self._pre_flight_checks()

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
                logger.error(f"Transaction failed: {str(e)}")
                logger.error(f"Full traceback: {traceback.format_exc()}")
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
        any_message = self._create_any_message(msg, type_url)

        tx = Transaction()
        tx.add_message(any_message)

        if gas_limit is None:
            gas_limit = await self._estimate_gas(type_url)

        gas_limit = int(gas_limit * gas_multiplier)
        fee = await self._calculate_optimal_fee(gas_limit, fee_tier)

        try:
            account_info = self.client.query_account(self.wallet.address())
            logger.debug(f"Account info: seq={account_info.sequence}, num={account_info.number}")

            logger.debug("Sealing transaction...")
            tx.seal(
                signing_cfgs=[SigningCfg.direct(self.wallet.public_key(), sequence_num=account_info.sequence)],
                fee=TxFee(amount=[ fee ], gas_limit=gas_limit),
            )
            logger.debug("Transaction sealed successfully")

            logger.debug("Signing transaction...")
            tx.sign(
                signer=self.wallet.signer(),
                chain_id=self.config.chain_id,
                account_number=account_info.number,
            )
            logger.debug("Transaction signed successfully")

            logger.debug("Completing transaction...")
            tx.complete()
            logger.debug("Transaction completed successfully")

            logger.debug("Broadcasting transaction...")
            broadcast_result = self.client.broadcast_tx(tx)
            logger.debug(f"Got broadcast_result: {broadcast_result}")
            logger.debug(f"broadcast_result type: {type(broadcast_result)}")
            
            if broadcast_result is None:
                raise TxError('broadcast_tx returned None - check network connectivity')
            
            # Get the transaction hash from broadcast
            tx_hash = broadcast_result.tx_hash
            logger.info(f"📡 Transaction broadcast successful, hash: {tx_hash}")
            logger.info("⏳ Waiting for transaction to be included in block...")
            
            # Wait for the transaction to be included in a block
            import datetime
            timeout = datetime.timedelta(seconds=30)  # 30 second timeout
            poll_period = datetime.timedelta(seconds=1)  # Poll every 1 second
            
            # Try to wait for completion, but handle parsing errors gracefully
            try:
                completed_tx = broadcast_result.wait_to_complete(timeout=timeout, poll_period=poll_period)
                logger.info(f"✅ Transaction included in block!")
                
                if completed_tx is None or completed_tx.response is None:
                    raise TxError('Final transaction response is None after waiting')

                response = completed_tx.response
                
                # Log full response details for debugging
                logger.debug(f"📋 Transaction Response Details:")
                logger.debug(f"   - Code: {response.code}")
                logger.debug(f"   - Raw Log: {response.raw_log}")
                logger.debug(f"   - TX Hash: {response.txhash}")
                if hasattr(response, 'gas_used'):
                    logger.debug(f"   - Gas Used: {response.gas_used}")
                if hasattr(response, 'gas_wanted'):
                    logger.debug(f"   - Gas Wanted: {response.gas_wanted}")
                
            except Exception as e:
                # If we get a parsing error, the transaction might still have succeeded
                # Check if it's a descriptor/parsing error
                if "Can not find message descriptor" in str(e) or "Failed to parse" in str(e):
                    logger.warning(f"Transaction likely succeeded but cosmpy can't parse response: {e}")
                    
                    # Try to fetch the raw transaction to get real status
                    try:
                        import requests
                        tx_url = f"{self.config.url.replace('rest+https://', 'https://')}/cosmos/tx/v1beta1/txs/{tx_hash}"
                        logger.debug(f"🔍 Attempting to fetch raw transaction: {tx_url}")
                        resp = requests.get(tx_url, timeout=10)
                        if resp.status_code == 200:
                            tx_data = resp.json()
                            if 'tx_response' in tx_data:
                                tx_resp = tx_data['tx_response']
                                logger.warning(f"🔍 RAW TRANSACTION STATUS:")
                                logger.warning(f"   - Code: {tx_resp.get('code', 'unknown')}")
                                logger.warning(f"   - Raw Log: {tx_resp.get('raw_log', 'unknown')}")
                                logger.warning(f"   - Gas Used: {tx_resp.get('gas_used', 'unknown')}")
                                logger.warning(f"   - Gas Wanted: {tx_resp.get('gas_wanted', 'unknown')}")
                                
                                # If code != 0, it failed
                                if tx_resp.get('code', 0) != 0:
                                    logger.error(f"❌ Transaction FAILED with code {tx_resp.get('code')}")
                                    logger.error(f"❌ Failure reason: {tx_resp.get('raw_log', 'unknown')}")
                                    raise TxError(f"Transaction failed: {tx_resp.get('raw_log', 'unknown')}")
                                else:
                                    logger.info(f"✅ Transaction SUCCEEDED according to raw query!")
                        else:
                            logger.warning(f"Could not fetch transaction details (HTTP {resp.status_code})")
                    except Exception as fetch_e:
                        logger.warning(f"Failed to fetch raw transaction details: {fetch_e}")
                    
                    logger.info(f"✅ Transaction hash {tx_hash} was submitted successfully!")
                    logger.info("📋 Check transaction status manually if needed")
                    
                    # Create a minimal successful response for the client
                    class MockResponse:
                        code = 0
                        raw_log = "Transaction submitted successfully (parsing skipped due to cosmpy descriptor issue)"
                        txhash = tx_hash
                        
                    return MockResponse()
                else:
                    # Re-raise other types of errors
                    raise

            if response.code != 0:
                # Parse common error types
                if "out of gas" in response.raw_log.lower():
                    raise OutOfGasError(f"Transaction ran out of gas: {response.raw_log}")
                elif "account sequence mismatch" in response.raw_log.lower():
                    raise AccountSequenceMismatchError(f"Sequence mismatch: {response.raw_log}")
                else:
                    raise TxError(f"Transaction failed (code {response.code}): {response.raw_log}")

            return response

        except (OutOfGasError, AccountSequenceMismatchError):
            # Re-raise these for retry logic
            raise
        except Exception as e:
            logger.error(f"Transaction submission error: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
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


    def _create_any_message(self, message, type_url: str):
        """
        Convert a betterproto message to a format cosmpy can handle without double-wrapping.

        Args:
            message: Betterproto message instance
            type_url: The type URL for the message

        Returns:
            Wrapper object that cosmpy can use for internal Any-wrapping
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
                        logger.debug(f"Mock descriptor created with full_name: {self.full_name}")
                        
                return MockDescriptor(self._type_url)
        
        wrapped_message = BetterprotoWrapper(message, type_url)
        logger.debug(f"Created wrapper for type_url: {type_url}")
        return wrapped_message
