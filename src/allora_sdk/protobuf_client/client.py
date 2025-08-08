"""
Allora Protobuf Client

This module provides the main ProtobufClient class which wraps cosmpy's LedgerClient
and provides Allora-specific functionality for interacting with the blockchain.
"""

import logging
import os
from typing import Optional, Dict

from cosmpy.aerial.client import LedgerClient, ValidatorStatus
from cosmpy.aerial.types import Account
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.keypairs import PrivateKey
from grpclib.client import Channel

from .client_emissions import EmissionsClient
from .client_mint import MintClient

from .config import AlloraNetworkConfig, DEFAULT_TESTNET_CONFIG
from .events import AlloraWebsocketSubscriber
from .queries import AlloraQueries
from .utils import AlloraUtils

logger = logging.getLogger(__name__)


class ProtobufClient:
    """
    Main client for interacting with the Allora blockchain.
    
    This class provides a high-level interface for blockchain operations
    including queries, transactions, and event subscriptions.
    """
    
    def __init__(
        self,
        config: Optional[AlloraNetworkConfig] = None,
        private_key: Optional[str] = None,
        mnemonic: Optional[str] = None,
        debug: bool = False
    ):
        """
        Initialize the Allora blockchain client.
        
        Args:
            config: Network configuration. If None, uses testnet config.
            private_key: Hex-encoded private key for signing transactions.
            mnemonic: Mnemonic phrase for generating wallet.
            debug: Enable debug logging.
        """
        self.debug = debug
        if debug:
            logging.basicConfig(level=logging.DEBUG)
        
        # Set up network configuration
        self.config = config or self._get_default_config()
        self.grpc_channel = Channel(host="100.126.144.65", port=9090, ssl=False)
        
        # Initialize cosmpy client
        self.ledger_client = LedgerClient(cfg=self.config.to_cosmpy_config())
        
        # Initialize wallet if credentials provided
        self.wallet: Optional[LocalWallet] = None
        if private_key or mnemonic:
            self._initialize_wallet(private_key, mnemonic)

        # Set up gRPC services
        self.emissions = EmissionsClient(wallet=self.wallet, client=self.ledger_client, grpc_channel=self.grpc_channel, config=self.config)
        self.mint = MintClient(wallet=self.wallet, client=self.ledger_client, grpc_channel=self.grpc_channel, config=self.config)
        
        # Initialize submodules
        self.queries = AlloraQueries(self)
        self.events = AlloraWebsocketSubscriber(self)
        self.utils = AlloraUtils(self)
        
        logger.info(f"Initialized Allora client for {self.config.chain_id}")

    
    def _get_default_config(self) -> AlloraNetworkConfig:
        """Get default configuration from environment or use testnet."""
        if any(key.startswith("ALLORA_") for key in os.environ):
            return AlloraNetworkConfig.from_env()
        return DEFAULT_TESTNET_CONFIG
    

    def _initialize_wallet(self, private_key: Optional[str], mnemonic: Optional[str]):
        """Initialize wallet from private key or mnemonic."""
        try:
            if private_key:
                pk = PrivateKey(bytes.fromhex(private_key))
                self.wallet = LocalWallet(pk, prefix="allo")
                logger.info("Wallet initialized from private key")
            elif mnemonic:
                self.wallet = LocalWallet.from_mnemonic(mnemonic, prefix="allo")
                logger.info("Wallet initialized from mnemonic")
        except Exception as e:
            logger.error(f"Failed to initialize wallet: {e}")
            raise ValueError(f"Invalid wallet credentials: {e}")
    

    @property
    def address(self) -> Optional[str]:
        """Get the wallet address if wallet is initialized."""
        return str(self.wallet.address()) if self.wallet else None

    
    @property
    def public_key(self) -> Optional[str]:
        """Get the wallet public key if wallet is initialized."""
        if self.wallet:
            return self.wallet.public_key().public_key_hex
        return None
    

    def is_connected(self) -> bool:
        """Check if client is connected to the network."""
        try:
            # Try to get chain ID to test connection
            chain_id = self.ledger_client.query_chain_id()
            return chain_id == self.config.chain_id
        except Exception:
            return False
    

    def get_latest_block(self):
        """Get latest block information."""
        return self.ledger_client.query_latest_block()

    
    def get_balance(self, address: Optional[str] = None, denom: Optional[str] = None) -> Dict[str, int]:
        """
        Get account balance(s).
        
        Args:
            address: Account address. If None, uses wallet address.
            denom: Specific denomination to query. If None, returns all balances.
            
        Returns:
            Dictionary mapping denomination to amount.
        """
        if not address and not self.wallet:
            raise ValueError("Address required when no wallet is configured")

        try:
            if denom:
                balance = self.ledger_client.query_bank_balance(self.wallet.address(), denom)
                return {denom: balance}
            else:
                balances = self.ledger_client.query_bank_all_balances(self.wallet.address())
                return {coin.denom: coin.amount for coin in balances}
        except Exception as e:
            logger.error(f"Failed to get balance for {self.wallet.address()}: {e}")
            return {}
    

    def get_account_info(self, address: Optional[str] = None) -> Account:
        """
        Get account information including sequence number.

        Args:
            address: Account address. If None, uses wallet address.

        Returns:
            An `Account` object.
        """
        if not address and not self.wallet:
            raise ValueError("Address required when no wallet is configured")
        
        return self.ledger_client.query_account(self.wallet.address())


    def get_validators(self, status: ValidatorStatus = ValidatorStatus.BONDED):
        """
        Get list of validators.

        Args:
            status: Validator status filter

        Returns:
            List of validator information
        """
        return self.ledger_client.query_validators(status)


    async def close(self):
        """Close client and cleanup resources."""
        logger.info("Closing Allora client")
        if self.events:
            await self.events.stop()
        self.grpc_channel.close()


    @classmethod
    def from_mnemonic(
        cls,
        mnemonic: str,
        config: Optional[AlloraNetworkConfig] = None,
        debug: bool = False
    ) -> 'ProtobufClient':
        """Create client from mnemonic phrase."""
        return cls(config=config, mnemonic=mnemonic, debug=debug)
    

    @classmethod
    def from_private_key(
        cls,
        private_key: str,
        config: Optional[AlloraNetworkConfig] = None,
        debug: bool = False
    ) -> 'ProtobufClient':
        """Create client from private key."""
        return cls(config=config, private_key=private_key, debug=debug)
    

    @classmethod
    def testnet(
        cls,
        private_key: Optional[str] = None,
        mnemonic: Optional[str] = None,
        debug: bool = False
    ) -> 'ProtobufClient':
        """Create client for testnet."""
        return cls(
            config=AlloraNetworkConfig.testnet(),
            private_key=private_key,
            mnemonic=mnemonic,
            debug=debug
        )
    

    @classmethod
    def mainnet(
        cls,
        private_key: Optional[str] = None,
        mnemonic: Optional[str] = None,
        debug: bool = False
    ) -> 'ProtobufClient':
        """Create client for mainnet."""
        return cls(
            config=AlloraNetworkConfig.mainnet(),
            private_key=private_key,
            mnemonic=mnemonic,
            debug=debug
        )
    
    @classmethod
    def local(
        cls,
        port: int = 26657,
        private_key: Optional[str] = None,
        mnemonic: Optional[str] = None,
        debug: bool = False
    ) -> 'ProtobufClient':
        """Create client for local development."""
        return cls(
            config=AlloraNetworkConfig.local(port),
            private_key=private_key,
            mnemonic=mnemonic,
            debug=debug
        )




