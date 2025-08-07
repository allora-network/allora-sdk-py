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
    rpc_endpoint: str
    rest_endpoint: str
    websocket_endpoint: str
    fee_denom: str = "uallo"
    fee_minimum_gas_price: float = 0.025
    
    @classmethod
    def testnet(cls) -> 'AlloraNetworkConfig':
        """Get Allora testnet configuration."""
        return cls(
            chain_id="allora-testnet-1",
            rpc_endpoint="rest+https://allora-rpc.testnet.allora.network",
            rest_endpoint="https://allora-api.testnet.allora.network",
            websocket_endpoint="wss://allora-rpc.testnet.allora.network/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=0.025
        )
    
    @classmethod
    def mainnet(cls) -> 'AlloraNetworkConfig':
        """Get Allora mainnet configuration."""
        return cls(
            chain_id="allora-mainnet-1",
            rpc_endpoint="rest+https://allora-rpc.mainnet.allora.network",
            rest_endpoint="https://allora-api.mainnet.allora.network",
            websocket_endpoint="wss://allora-rpc.mainnet.allora.network/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=0.025
        )
    
    @classmethod
    def local(cls, port: int = 26657) -> 'AlloraNetworkConfig':
        """Get local development configuration."""
        return cls(
            chain_id="allora-local",
            rpc_endpoint=f"http://localhost:{port}",
            rest_endpoint=f"http://localhost:{port + 1}",
            websocket_endpoint=f"ws://localhost:{port}/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=0.0
        )
    
    @classmethod
    def from_env(cls) -> 'AlloraNetworkConfig':
        """Create configuration from environment variables."""
        return cls(
            chain_id=os.getenv("ALLORA_CHAIN_ID", "allora-testnet-1"),
            rpc_endpoint=os.getenv("ALLORA_RPC_ENDPOINT", "https://allora-rpc.testnet.allora.network:443"),
            rest_endpoint=os.getenv("ALLORA_REST_ENDPOINT", "https://allora-api.testnet.allora.network"),
            websocket_endpoint=os.getenv("ALLORA_WEBSOCKET_ENDPOINT", "wss://allora-rpc.testnet.allora.network:443/websocket"),
            fee_denom=os.getenv("ALLORA_FEE_DENOM", "uallo"),
            fee_minimum_gas_price=float(os.getenv("ALLORA_FEE_MIN_GAS_PRICE", "0.025"))
        )
    
    def to_cosmpy_config(self) -> NetworkConfig:
        """Convert to cosmpy NetworkConfig."""
        return NetworkConfig(
            chain_id=self.chain_id,
            url=self.rpc_endpoint,
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