"""
Allora Network Configuration

This module provides network configuration classes for connecting to
different Allora blockchain networks (testnet, mainnet, local).
"""

import os
from dataclasses import dataclass
from typing import Optional
from cosmpy.aerial.config import NetworkConfig


@dataclass
class AlloraNetworkConfig:
    """Configuration for Allora blockchain networks."""
    
    chain_id: str
    url: str
    websocket_url: str
    fee_denom: str = "uallo"
    fee_minimum_gas_price: float = 10.0
    
    @classmethod
    def testnet(cls) -> 'AlloraNetworkConfig':
        """Get Allora testnet configuration."""
        return cls(
            chain_id="allora-testnet-1",
            # url="grpc+http://65.108.224.166:26757",
            # url="grpc+http://allora-grpc.polkachu.com:26790",
            url="grpc+http://100.126.144.65:9090",
            # url="grpc+http://100.126.144.65:9090",
            websocket_url="wss://allora-rpc.testnet.allora.network/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=10.0
        )

    @classmethod
    def mainnet(cls) -> 'AlloraNetworkConfig':
        """Get Allora mainnet configuration."""
        return cls(
            chain_id="allora-mainnet-1",
            url="rest+https://allora-rpc.mainnet.allora.network",
            websocket_url="wss://allora-rpc.mainnet.allora.network/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=10.0
        )

    @classmethod
    def local(cls, port: int = 26657) -> 'AlloraNetworkConfig':
        """Get local development configuration."""
        return cls(
            chain_id="allora-local",
            url=f"http://localhost:{port}",
            websocket_url=f"ws://localhost:{port}/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=0.0
        )

    @classmethod
    def from_env(cls) -> 'AlloraNetworkConfig':
        """Create configuration from environment variables."""
        return cls(
            chain_id=os.getenv("ALLORA_CHAIN_ID", "allora-testnet-1"),
            url=os.getenv("ALLORA_RPC_ENDPOINT", "https://allora-rpc.testnet.allora.network:443"),
            websocket_url=os.getenv("ALLORA_WEBSOCKET_ENDPOINT", "wss://allora-rpc.testnet.allora.network:443/websocket"),
            fee_denom=os.getenv("ALLORA_FEE_DENOM", "uallo"),
            fee_minimum_gas_price=float(os.getenv("ALLORA_FEE_MIN_GAS_PRICE", "0.025"))
        )
    
    def to_cosmpy_config(self) -> NetworkConfig:
        """Convert to cosmpy NetworkConfig."""
        return NetworkConfig(
            chain_id=self.chain_id,
            url=self.url,
            fee_minimum_gas_price=self.fee_minimum_gas_price,
            fee_denomination=self.fee_denom,
            staking_denomination=self.fee_denom
        )


class AlloraEndpoints:
    """Common Allora blockchain endpoints and paths."""
    
    # Query endpoints
    BANK_BALANCE = "/cosmos/bank/v1beta1/balances/{address}"
    BANK_SUPPLY = "/cosmos/bank/v1beta1/supply"
    
    # Allora-specific endpoints (these would be actual Allora module endpoints)
    INFERENCE_REQUESTS = "/allora/inference/v1/requests"
    MODEL_REGISTRATIONS = "/allora/models/v1/registrations"
    WORKER_STATUS = "/allora/workers/v1/status"
    VALIDATOR_INFO = "/allora/validators/v1/info"
    
    # Staking endpoints
    STAKING_VALIDATORS = "/cosmos/staking/v1beta1/validators"
    STAKING_DELEGATIONS = "/cosmos/staking/v1beta1/delegations/{delegator_addr}"
    
    # Governance endpoints
    GOV_PROPOSALS = "/cosmos/gov/v1beta1/proposals"
    
    @staticmethod
    def get_balance_endpoint(address: str) -> str:
        """Get balance query endpoint for address."""
        return AlloraEndpoints.BANK_BALANCE.format(address=address)
    
    @staticmethod
    def get_delegations_endpoint(delegator_addr: str) -> str:
        """Get delegations query endpoint for delegator."""
        return AlloraEndpoints.STAKING_DELEGATIONS.format(delegator_addr=delegator_addr)


# Default configurations
DEFAULT_TESTNET_CONFIG = AlloraNetworkConfig.testnet()
DEFAULT_MAINNET_CONFIG = AlloraNetworkConfig.mainnet()
DEFAULT_LOCAL_CONFIG = AlloraNetworkConfig.local()