"""
Allora Protobuf Client

This module provides the main AlloraRPCClient class which wraps cosmpy's LedgerClient
and provides Allora-specific functionality for interacting with the blockchain.
"""

import logging
from typing import Optional

import grpc
import certifi
from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.urls import Protocol, parse_url
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.keypairs import PrivateKey

import allora_sdk.protos.cosmos.base.tendermint.v1beta1 as tendermint_v1beta1
import allora_sdk.protos.cosmos.tx.v1beta1 as cosmos_tx_v1beta1
import allora_sdk.protos.cosmos.auth.v1beta1 as cosmos_auth_v1beta1
import allora_sdk.protos.cosmos.bank.v1beta1 as cosmos_bank_v1beta1
import allora_sdk.protos.emissions.v9 as emissions_v9
import allora_sdk.protos.mint.v5 as mint_v5
import allora_sdk.rest as rest

from .client_emissions import EmissionsClient
from .client_mint import MintClient
from .config import AlloraNetworkConfig, AlloraWalletConfig
from .client_websocket_events import AlloraWebsocketSubscriber
from .tx_manager import TxManager

logger = logging.getLogger("allora_sdk")


class AlloraRPCClient:
    """
    Main client for interacting with the Allora blockchain.
    
    This class provides a high-level interface for blockchain operations
    including queries, transactions, and event subscriptions.
    """

    wallet: Optional[LocalWallet] = None
    tx_manager: Optional[TxManager] = None

    def __init__(
        self,
        network: Optional[AlloraNetworkConfig] = None,
        wallet: Optional[AlloraWalletConfig] = None,
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
        if debug:
            logging.basicConfig(level=logging.DEBUG)
        
        self.network = network if network is not None else AlloraNetworkConfig.testnet()
        self.ledger_client = LedgerClient(cfg=self.network.to_cosmpy_config())
        self._initialize_wallet(wallet)

        parsed_url = parse_url(self.network.url)

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

        if self.network.websocket_url is not None:
            self.events = AlloraWebsocketSubscriber(self.network.websocket_url)

        if self.wallet is not None:
            self.tx_manager = TxManager(
                wallet=self.wallet,
                tx_client=self.tx,
                auth_client=self.auth,
                bank_client=self.bank,
                config=self.network,
            )

        self.emissions = EmissionsClient(query_client=emissions, tx_manager=self.tx_manager)
        self.mint = MintClient(query_client=mint)
        # self.cosmos_tx = CosmosTxClient(query_client=cosmos_tx)
        
        logger.info(f"Initialized Allora client for {self.network.chain_id}")
    

    def _initialize_wallet(self, wallet: Optional[AlloraWalletConfig]):
        """Initialize wallet from private key or mnemonic."""
        if not wallet:
            return

        try:
            if wallet.wallet:
                self.wallet = wallet.wallet
                logger.info("Wallet initialized from LocalWallet")
            elif wallet.private_key:
                pk = PrivateKey(bytes.fromhex(wallet.private_key))
                self.wallet = LocalWallet(pk, prefix="allo")
                logger.info("Wallet initialized from private key")
            elif wallet.mnemonic:
                self.wallet = LocalWallet.from_mnemonic(wallet.mnemonic, prefix="allo")
                logger.info("Wallet initialized from mnemonic")
            elif wallet.mnemonic_file:
                with open(wallet.mnemonic_file) as f:
                    mnemonic = f.read()
                self.wallet = LocalWallet.from_mnemonic(mnemonic, prefix="allo")
                logger.info("Wallet initialized from mnemonic file")
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
            chain_id = self.get_latest_block().header.chain_id
            return chain_id == self.network.chain_id
        except Exception:
            return False
    

    def get_latest_block(self):
        resp = self.tendermint.get_latest_block()
        if resp is None or resp.block is None:
            raise Exception('could not get latest block')
        return resp.block


    async def close(self):
        """Close client and cleanup resources."""
        logger.debug("Closing Allora client")
        if self.events:
            await self.events.stop()
        if self.grpc_client:
            self.grpc_client.close()


    @classmethod
    def testnet(
        cls,
        wallet: Optional[AlloraWalletConfig] = None,
        debug: bool = True,
    ) -> 'AlloraRPCClient':
        """Create client for testnet."""
        return cls(
            network=AlloraNetworkConfig.testnet(),
            wallet=wallet,
            debug=debug
        )


    @classmethod
    def mainnet(
        cls,
        wallet: Optional[AlloraWalletConfig] = None,
        debug: bool = True,
    ) -> 'AlloraRPCClient':
        """Create client for mainnet."""
        return cls(
            network=AlloraNetworkConfig.mainnet(),
            wallet=wallet,
            debug=debug
        )

    @classmethod
    def local(
        cls,
        port: int = 26657,
        wallet: Optional[AlloraWalletConfig] = None,
        debug: bool = True,
    ) -> 'AlloraRPCClient':
        """Create client for local development."""
        return cls(
            network=AlloraNetworkConfig.local(port),
            wallet=wallet,
            debug=debug
        )

    @classmethod
    def from_env(
        cls,
        network: Optional[AlloraNetworkConfig] = None,
        wallet: Optional[AlloraWalletConfig] = None,
        debug: bool = False,
    ) -> 'AlloraRPCClient':
        """Create client using environment variables."""
        if network is None:
            network = AlloraNetworkConfig.from_env()
        if wallet is None:
            wallet = AlloraWalletConfig.from_env()
        return cls(network=network, wallet=wallet, debug=debug)
