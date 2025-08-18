"""
Allora Protobuf Client

This module provides the main ProtobufClient class which wraps cosmpy's LedgerClient
and provides Allora-specific functionality for interacting with the blockchain.
"""

import logging
import os
from typing import Optional, Dict

import certifi
from cosmpy.aerial.client import LedgerClient, ValidatorStatus
from cosmpy.aerial.urls import Protocol, parse_url
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.keypairs import PrivateKey
import grpc

from allora_sdk.protobuf_client.client_mint import MintClient
import allora_sdk.protobuf_client.protos.cosmos.base.tendermint.v1beta1 as tendermint_v1beta1
import allora_sdk.protobuf_client.protos.cosmos.tx.v1beta1 as cosmos_tx_v1beta1
import allora_sdk.protobuf_client.protos.cosmos.auth.v1beta1 as cosmos_auth_v1beta1
import allora_sdk.protobuf_client.protos.cosmos.bank.v1beta1 as cosmos_bank_v1beta1
import allora_sdk.protobuf_client.protos.emissions.v9 as emissions_v9
import allora_sdk.protobuf_client.protos.mint.v5 as mint_v5
import allora_sdk.rest as rest

from .client_emissions import EmissionsClient
from .config import AlloraNetworkConfig, DEFAULT_TESTNET_CONFIG
from .client_websocket_events import AlloraWebsocketSubscriber
from .utils import AlloraUtils
from .tx_manager import TxManager

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
        log_level: logging._Level = logging.INFO,
    ):
        """
        Initialize the Allora blockchain client.
        
        Args:
            config: Network configuration. If None, uses testnet config.
            private_key: Hex-encoded private key for signing transactions.
            mnemonic: Mnemonic phrase for generating wallet.
            debug: Enable debug logging.
        """
        logging.basicConfig(level=log_level)
        
        # Set up network configuration
        self.config = config or self._get_default_config()

        # Initialize cosmpy client
        self.ledger_client = LedgerClient(cfg=self.config.to_cosmpy_config())

        # Initialize wallet if credentials provided
        self.wallet: Optional[LocalWallet] = None
        if private_key or mnemonic:
            self._initialize_wallet(private_key, mnemonic)

        parsed_url = parse_url(self.config.url)

        if parsed_url.protocol == Protocol.GRPC:
            if parsed_url.secure:
                with open(certifi.where(), "rb") as f:
                    trusted_certs = f.read()
                credentials = grpc.ssl_channel_credentials(root_certificates=trusted_certs)
                self.grpc_client = grpc.secure_channel(parsed_url.host_and_port, credentials)
            else:
                self.grpc_client = grpc.insecure_channel(parsed_url.host_and_port)

            # Set up gRPC services
            emissions: rest.EmissionsV9QueryServiceLike = emissions_v9.QueryServiceStub(self.grpc_client)
            mint: rest.MintV5QueryServiceLike = mint_v5.QueryServiceStub(self.grpc_client)
            self.tx: rest.CosmosTxV1Beta1ServiceLike = cosmos_tx_v1beta1.ServiceStub(self.grpc_client)
            self.tendermint: rest.CosmosBaseTendermintV1Beta1ServiceLike = tendermint_v1beta1.ServiceStub(self.grpc_client)
            self.auth = cosmos_auth_v1beta1.QueryStub(self.grpc_client)
            self.bank = cosmos_bank_v1beta1.QueryStub(self.grpc_client)
        else:
            # Set up REST (Cosmos-LCD) services
            emissions: rest.EmissionsV9QueryServiceLike = rest.EmissionsV9RestQueryServiceClient(parsed_url.rest_url)
            mint: rest.MintV5QueryServiceLike = rest.MintV5RestQueryServiceClient(parsed_url.rest_url)
            self.tx: rest.CosmosTxV1Beta1ServiceLike = rest.CosmosTxV1Beta1RestServiceClient(parsed_url.rest_url)
            self.tendermint: rest.CosmosBaseTendermintV1Beta1ServiceLike = rest.CosmosBaseTendermintV1Beta1RestServiceClient(parsed_url.rest_url)
            self.auth: rest.CosmosAuthV1Beta1QueryLike = rest.CosmosAuthV1Beta1RestQueryClient(parsed_url.rest_url)
            self.bank: rest.CosmosBankV1Beta1QueryLike = rest.CosmosBankV1Beta1RestQueryClient(parsed_url.rest_url)

        tendermint = tendermint_v1beta1.ServiceStub(self.grpc_client)

        # self._response = self._client.wait_for_query_tx(
        #     self.tx_hash, timeout=timeout, poll_period=poll_period
        # )
        # assert self._response is not None
        # self._response.ensure_successful()

        self.tx_manager = TxManager(
            wallet=self.wallet,
            tx_client=self.tx,
            auth_client=self.auth,
            bank_client=self.bank,
            config=self.config,
        )
        self.events = AlloraWebsocketSubscriber(self.config.websocket_url)
        self.utils = AlloraUtils(self)
        self.emissions = EmissionsClient(query_client=emissions, tx_manager=self.tx_manager)
        self.mint = MintClient(query_client=mint)
        # self.cosmos_tx = CosmosTxClient(query_client=cosmos_tx)
        
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
        resp = self.tendermint.get_latest_block(tendermint_v1beta1.GetLatestBlockRequest())
        if resp is None or resp.block is None:
            raise Exception('could not get latest block')
        return resp.block

    
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
    

    async def get_account_info(self, address: Optional[str] = None):
        """
        Get account information including sequence number.

        Args:
            address: Account address. If None, uses wallet address.

        Returns:
            An `Account` object.
        """
        if not address and not self.wallet:
            raise ValueError("Address required when no wallet is configured")
        
        # Prefer gRPC auth service to fetch account metadata, then return cosmpy Account for compatibility
        # target = address or str(self.wallet.address())
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
        logger.debug("Closing Allora client")
        if self.events:
            await self.events.stop()
        self.grpc_client.close()


    @classmethod
    def from_mnemonic(
        cls,
        mnemonic: str,
        config: Optional[AlloraNetworkConfig] = None,
        log_level: logging._Level = logging.INFO,
    ) -> 'ProtobufClient':
        """Create client from mnemonic phrase."""
        return cls(config=config, mnemonic=mnemonic, log_level=log_level,)
    

    @classmethod
    def from_private_key(
        cls,
        private_key: str,
        config: Optional[AlloraNetworkConfig] = None,
        log_level: logging._Level = logging.INFO,
    ) -> 'ProtobufClient':
        """Create client from private key."""
        return cls(config=config, private_key=private_key, log_level=log_level,)
    

    @classmethod
    def testnet(
        cls,
        private_key: Optional[str] = None,
        mnemonic: Optional[str] = None,
        log_level: logging._Level = logging.INFO,
    ) -> 'ProtobufClient':
        """Create client for testnet."""
        return cls(
            config=AlloraNetworkConfig.testnet(),
            private_key=private_key,
            mnemonic=mnemonic,
            log_level=log_level,
        )
    

    @classmethod
    def mainnet(
        cls,
        private_key: Optional[str] = None,
        mnemonic: Optional[str] = None,
        log_level: logging._Level = logging.INFO,
    ) -> 'ProtobufClient':
        """Create client for mainnet."""
        return cls(
            config=AlloraNetworkConfig.mainnet(),
            private_key=private_key,
            mnemonic=mnemonic,
            log_level=log_level,
        )
    
    @classmethod
    def local(
        cls,
        port: int = 26657,
        private_key: Optional[str] = None,
        mnemonic: Optional[str] = None,
        log_level: logging._Level = logging.INFO,
    ) -> 'ProtobufClient':
        """Create client for local development."""
        return cls(
            config=AlloraNetworkConfig.local(port),
            private_key=private_key,
            mnemonic=mnemonic,
            log_level=log_level,
        )




