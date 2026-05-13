"""
Allora RPC Client

This module provides the main AlloraRPCClient class which wraps either a gRPC
or REST client and provides Allora-specific functionality for interacting with
the blockchain.

The client can make queries, submit transactions, and subscribe to websocket events
provided it is given the appropriate configuration.
"""

import logging
from typing import Optional

from grpclib.client import Channel
from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.urls import Protocol, parse_url
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.keypairs import PrivateKey

import allora_sdk.rpc_client.protos.cosmos.base.tendermint.v1beta1 as tendermint_v1beta1
import allora_sdk.rpc_client.protos.cosmos.tx.v1beta1 as cosmos_tx_v1beta1
import allora_sdk.rpc_client.protos.cosmos.auth.v1beta1 as cosmos_auth_v1beta1
import allora_sdk.rpc_client.protos.cosmos.bank.v1beta1 as cosmos_bank_v1beta1
import allora_sdk.rpc_client.protos.emissions.v9 as emissions_v9
import allora_sdk.rpc_client.protos.feemarket.feemarket.v1 as feemarket_v1
import allora_sdk.rpc_client.protos.mint.v5 as mint_v5
import allora_sdk.rpc_client.rest as rest
from allora_sdk.rpc_client.client_auth import AuthClient
from allora_sdk.rpc_client.client_bank import BankClient
from allora_sdk.rpc_client.client_feemarket import FeemarketClient
from allora_sdk.rpc_client.client_tx import TxClient
from allora_sdk.rpc_client.client_tendermint import TendermintClient

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
        wallet: Optional[AlloraWalletConfig] = None,
        network: AlloraNetworkConfig = AlloraNetworkConfig.testnet(),
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

        self.network = network
        self.ledger_client = LedgerClient(cfg=self.network.to_cosmpy_config())
        self._initialize_wallet(wallet)

        self._parsed_url = parse_url(self.network.url)
        self.grpc_client: Optional[Channel] = None

        self._build_clients()

        if self.network.websocket_url is not None:
            self.events = AlloraWebsocketSubscriber(self.network.websocket_url)

        logger.debug(f"Initialized Allora client for {self.network.chain_id}")


    def _build_clients(self):
        """
        (Re)build the gRPC / REST query stubs and the TxManager.

        Called once from __init__ and again from _reconnect_grpc() when a
        transport-layer failure forces us to dial a new channel. Splitting
        this out keeps the construction logic in one place so the
        reconnect path can't drift from the initial-setup path.
        """
        parsed_url = self._parsed_url

        if parsed_url.protocol == Protocol.GRPC:
            self.grpc_client = Channel(host=parsed_url.hostname, port=parsed_url.port, ssl=parsed_url.secure)

            # Set up gRPC services
            auth_query: rest.CosmosAuthV1Beta1QueryLike = cosmos_auth_v1beta1.QueryStub(self.grpc_client)
            bank_query: rest.CosmosBankV1Beta1QueryLike = cosmos_bank_v1beta1.QueryStub(self.grpc_client)
            tendermint_query: rest.CosmosBaseTendermintV1Beta1ServiceLike = tendermint_v1beta1.ServiceStub(self.grpc_client)
            tx_query: rest.CosmosTxV1Beta1ServiceLike = cosmos_tx_v1beta1.ServiceStub(self.grpc_client)
            emissions_query: rest.EmissionsV9QueryServiceLike = emissions_v9.QueryServiceStub(self.grpc_client)
            mint_query: rest.MintV5QueryServiceLike = mint_v5.QueryServiceStub(self.grpc_client)
            feemarket_query: rest.FeemarketFeemarketV1QueryLike = feemarket_v1.QueryStub(self.grpc_client)
        else:
            # Set up REST (Cosmos-LCD) services
            auth_query: rest.CosmosAuthV1Beta1QueryLike = rest.CosmosAuthV1Beta1RestQueryClient(parsed_url.rest_url)
            bank_query: rest.CosmosBankV1Beta1QueryLike = rest.CosmosBankV1Beta1RestQueryClient(parsed_url.rest_url)
            tendermint_query: rest.CosmosBaseTendermintV1Beta1ServiceLike = rest.CosmosBaseTendermintV1Beta1RestServiceClient(parsed_url.rest_url)
            tx_query: rest.CosmosTxV1Beta1ServiceLike = rest.CosmosTxV1Beta1RestServiceClient(parsed_url.rest_url)
            emissions_query: rest.EmissionsV9QueryServiceLike = rest.EmissionsV9RestQueryServiceClient(parsed_url.rest_url)
            mint_query: rest.MintV5QueryServiceLike = rest.MintV5RestQueryServiceClient(parsed_url.rest_url)
            feemarket_query: rest.FeemarketFeemarketV1QueryLike = rest.FeemarketFeemarketV1RestQueryClient(parsed_url.rest_url)

        # If we already have a tx_manager (reconnect path), re-bind its
        # stub references in-place so the existing instance stays valid
        # for any caller that captured it. Otherwise construct fresh.
        if self.tx_manager is not None:
            self.tx_manager.tx_client = tx_query
            self.tx_manager.auth_client = auth_query
            self.tx_manager.bank_client = bank_query
            self.tx_manager.feemarket_client = feemarket_query
        elif self.wallet is not None:
            self.tx_manager = TxManager(
                wallet=self.wallet,
                tx_client=tx_query,
                auth_client=auth_query,
                bank_client=bank_query,
                feemarket_client=feemarket_query,
                config=self.network,
                reconnect_callback=self._reconnect_grpc,
            )

        self.auth       = AuthClient(query_client=auth_query, tx_manager=self.tx_manager)
        self.bank       = BankClient(query_client=bank_query, tx_manager=self.tx_manager)
        self.tendermint = TendermintClient(query_client=tendermint_query, tx_manager=self.tx_manager)
        self.tx         = TxClient(query_client=tx_query, tx_manager=self.tx_manager)
        self.emissions  = EmissionsClient(query_client=emissions_query, tx_manager=self.tx_manager)
        self.mint       = MintClient(query_client=mint_query)
        self.feemarket  = FeemarketClient(query_client=feemarket_query)


    async def _reconnect_grpc(self):
        """
        Close the current grpclib.Channel and dial a fresh one, then re-bind
        all the gRPC stubs (and the TxManager's stub references) to the new
        channel.

        Called from TxManager when it observes a transport-layer failure
        (Cloudflare half-close, stream reset, server "Unavailable"). The
        caller is the only thing that knows the channel is dead — there's
        no notification from grpclib itself, since grpclib.Channel has no
        keepalive or connectivity-state machine.

        IMPORTANT: callers that captured a stub reference (e.g. via
        `self.tx`, `self.bank`, etc.) BEFORE the reconnect will continue
        holding the new stub because the Client* wrappers reference
        TxManager / query_client by attribute, and we mutate those in
        place above. New invocations against `self.tx.broadcast_tx(...)`
        will hit the new channel. Direct references to a Stub instance
        (rare) will still point at the dead one.

        This is REST-protocol-aware: if the configured URL is REST-only,
        there is no gRPC channel to recycle, and this is a no-op (the
        REST clients use pooled HTTP and don't have the same problem).
        """
        if self._parsed_url.protocol != Protocol.GRPC:
            logger.debug("Reconnect called but URL is REST-only — no gRPC channel to recycle")
            return

        old = self.grpc_client
        logger.warning(f"Reconnecting gRPC channel to {self.network.url}")
        try:
            if old is not None:
                old.close()
        except Exception as exc:
            # Closing a dead channel can itself raise; log and continue
            logger.debug(f"Closing old gRPC channel raised: {exc}")

        # Clear the reference so _build_clients dials a fresh one
        self.grpc_client = None
        self._build_clients()
    

    def _initialize_wallet(self, wallet: Optional[AlloraWalletConfig]):
        """Initialize wallet from private key or mnemonic."""
        if not wallet:
            return

        try:
            if wallet.wallet:
                self.wallet = wallet.wallet
                logger.debug("Wallet initialized from LocalWallet")
            elif wallet.private_key:
                pk = PrivateKey(bytes.fromhex(wallet.private_key))
                self.wallet = LocalWallet(pk, prefix="allo")
                logger.debug("Wallet initialized from private key")
            elif wallet.mnemonic:
                self.wallet = LocalWallet.from_mnemonic(wallet.mnemonic, prefix="allo")
                logger.debug("Wallet initialized from mnemonic")
            elif wallet.mnemonic_file:
                with open(wallet.mnemonic_file) as f:
                    mnemonic = f.read()
                self.wallet = LocalWallet.from_mnemonic(mnemonic, prefix="allo")
                logger.debug("Wallet initialized from mnemonic file")
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
        debug: bool = False,
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
        debug: bool = False,
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
        debug: bool = False,
    ) -> 'AlloraRPCClient':
        """Create client for local development."""
        return cls(
            network=AlloraNetworkConfig.local(port=port),
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
