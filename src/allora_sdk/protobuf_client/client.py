"""
Allora Protobuf Client

This module provides the main ProtobufClient class which wraps cosmpy's LedgerClient
and provides Allora-specific functionality for interacting with the blockchain.
"""

import logging
import os
from typing import List, Optional, Dict, Any

from cosmpy.aerial.client import LedgerClient, TxResponse, ValidatorStatus
from cosmpy.aerial.types import Account
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.keypairs import PrivateKey
from grpclib.client import Channel


from .config import AlloraNetworkConfig, DEFAULT_TESTNET_CONFIG
from .events import AlloraWebsocketSubscriber
from .queries import AlloraQueries
from .tx_manager import FeeTier, TxError, TxManager
from .utils import AlloraUtils

from allora_sdk.protobuf_client.proto.emissions.v3 import Nonce

from .proto.emissions.v9 import (
    QueryServiceStub as EmissionsQuerySvc,
    GetParamsRequest as EmissionsGetParamsRequest,
    InsertWorkerPayloadRequest,
)
from .proto.mint.v5 import (
    QueryServiceStub as MintQuerySvc,
    QueryServiceParamsRequest as MintGetParamsRequest,
    QueryServiceEmissionInfoRequest as MintEmissionInfoRequest,
    QueryServiceInflationRequest as MintInflationRequest,
)

from .proto.cosmos.bank.v1beta1 import (
    QueryStub as BankQuerySvc,
    QueryTotalSupplyRequest as BankTotalSupplyRequest,
)

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


    def get_validators(self, status: ValidatorStatus = ValidatorStatus.BONDED) -> List[Dict[str, Any]]:
        """
        Get list of validators.

        Args:
            status: Validator status filter

        Returns:
            List of validator information
        """
        return self.ledger_client.query_validators(status)


    async def start_event_subscription(self):
        """Start the event subscription service."""
        if not self.events:
            raise RuntimeError("Event subscriber not initialized")
        await self.events.start()
    
    async def stop_event_subscription(self):
        """Stop the event subscription service."""
        if self.events:
            await self.events.stop()
    
    def close(self):
        """Close client and cleanup resources."""
        logger.info("Closing Allora client")
        self.grpc_channel.close()
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
    
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

    # async def get_emissions_params(self):
    #     svc = QueryServiceStub(self.grpc_channel)
    #     response = await svc.get_params(GetParamsRequest())
    #     return response


from allora_sdk.protobuf_client.proto.emissions.v9 import (
    InputInference,
    InputInferenceForecastBundle,
    InputWorkerDataBundle,
    InsertWorkerPayloadRequest,
    InputForecastElement,
    InputForecast,
)

class EmissionsClient:
    """Client for Allora Emissions module operations."""
    
    def __init__(self, client: LedgerClient, grpc_channel: Channel, wallet: LocalWallet | None, config: AlloraNetworkConfig):
        self.query = EmissionsQuerySvc(grpc_channel)
        if wallet is not None:
            self.txs = TxManager(wallet=wallet, client=client, config=config)
        self.client = client

    async def params(self):
        """Get emissions module parameters."""
        return await self.query.get_params(EmissionsGetParamsRequest())

    async def insert_worker_payload(
        self, 
        topic_id: int, 
        inference_value: str,
        block_height: Optional[int] = None,
        forecast_elements: Optional[List[Dict[str, str]]] = None,
        extra_data: Optional[bytes] = None,
        proof: Optional[str] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
        gas_limit: Optional[int] = None
    ) -> TxResponse:
        """
        Submit a worker payload (inference/forecast) to the Allora network.
        
        Args:
            topic_id: The topic ID to submit inference for
            inference_value: The inference value as a string
            block_height: Block height for the inference (uses current if None)
            forecast_elements: Optional list of forecast elements 
                              [{"inferer": "address", "value": "prediction"}]
            extra_data: Optional extra data as bytes
            proof: Optional proof string
            fee_tier: Fee tier (ECO/STANDARD/PRIORITY) - defaults to STANDARD
            gas_limit: Manual gas limit override
            
        Returns:
            Transaction response with hash and status
        """
        if not self.txs:
            raise TxError('No wallet configured. Initialize client with private key or mnemonic.')

        # Get current block height if not provided
        if block_height is None:
            try:
                latest_block = self.client.query_latest_block()
                block_height = latest_block.height
                logger.info(f"Using current block height: {block_height}")
            except Exception as e:
                logger.error(f"Failed to get latest block height: {e}")
                raise TxError("Could not determine block height for inference")

        worker_address = str(self.txs.wallet.address())
        
        # Create InputInference
        inference = InputInference(
            topic_id=topic_id,
            block_height=block_height,
            inferer=worker_address,
            value=inference_value,
            extra_data=extra_data or b"",
            proof=proof or ""
        )
        
        if forecast_elements:
            forecast_elems = [
                InputForecastElement(
                    inferer=elem["inferer"],
                    value=elem["value"]
                )
                for elem in forecast_elements
            ]
        else:
            forecast_elems = []
            
        forecast = InputForecast(
            topic_id=topic_id,
            block_height=block_height,
            forecaster=worker_address,
            forecast_elements=forecast_elems,
            extra_data=extra_data or b""
        )

        bundle = InputInferenceForecastBundle(
            inference=inference,
            forecast=forecast,
        )
        # sign bundle with pubkey
        bundle_sig = self.txs.wallet.signer().sign_digest(bytes(bundle))
        
        # Create the complete message structure
        worker_data_bundle = InputWorkerDataBundle(
            worker=worker_address,
            nonce=Nonce(block_height=block_height),
            topic_id=topic_id,
            inference_forecasts_bundle=bundle,
            inferences_forecasts_bundle_signature=bundle_sig,
            pubkey=self.txs.wallet.public_key().public_key_hex if self.txs.wallet.public_key() else ""
        )

        payload_request = InsertWorkerPayloadRequest(
            sender=worker_address,
            worker_data_bundle=worker_data_bundle
        )

        logger.info(f"🚀 Submitting worker payload for topic {topic_id}, inference: {inference_value}")
        
        return await self.txs.submit_transaction(
            type_url="/emissions.v9.InsertWorkerPayloadRequest",
            msg=payload_request,
            gas_limit=gas_limit,
            fee_tier=fee_tier
        )


class MintClient:
    def __init__(self, client: LedgerClient, grpc_channel: Channel, wallet: LocalWallet | None, config: AlloraNetworkConfig):
        self.client = client
        self.wallet = wallet
        if wallet:
            self.txs = TxManager(wallet=wallet, client=client, config=config)
        self.query = MintQuerySvc(grpc_channel)

    async def params(self):
        return await self.query.params(MintGetParamsRequest())

    async def emission_info(self):
        return await self.query.emission_info(MintEmissionInfoRequest())

    async def inflation(self):
        return await self.query.inflation(MintInflationRequest())


class BankClient:
    def __init__(self, client: LedgerClient, grpc_channel: Channel, wallet: LocalWallet | None, config: AlloraNetworkConfig):
        self.client = client
        self.wallet = wallet
        if wallet:
            self.txs = TxManager(wallet=wallet, client=client, config=config)
        self.query = BankQuerySvc(grpc_channel)

    async def total_supply(self):
        return await self.query.total_supply(BankTotalSupplyRequest())




