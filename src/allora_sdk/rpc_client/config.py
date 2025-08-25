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
    faucet_url: Optional[str] = None
    
    @classmethod
    def testnet(cls) -> 'AlloraNetworkConfig':
        return cls(
            chain_id="allora-testnet-1",
            url="grpc+https://allora-grpc.testnet.allora.network:443",
            websocket_url="wss://allora-rpc.testnet.allora.network/websocket",
            faucet_url="https://faucet.testnet.allora.network",
            fee_denom="uallo",
            fee_minimum_gas_price=10.0
        )

    @classmethod
    def mainnet(cls) -> 'AlloraNetworkConfig':
        return cls(
            chain_id="allora-mainnet-1",
            url="grpc+https://allora-grpc.testnet.allora.network:443",
            websocket_url="wss://allora-rpc.mainnet.allora.network/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=10.0
        )

    @classmethod
    def local(cls, port: int = 26657) -> 'AlloraNetworkConfig':
        return cls(
            chain_id="allora-local",
            url=f"grpc+http://localhost:{port}",
            websocket_url=f"ws://localhost:{port}/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=0.0
        )

    @classmethod
    def from_env(cls) -> 'AlloraNetworkConfig':
        return cls(
            chain_id=os.getenv("ALLORA_CHAIN_ID", "allora-testnet-1"),
            url=os.getenv("ALLORA_RPC_ENDPOINT", "https://allora-grpc.testnet.allora.network:443"),
            websocket_url=os.getenv("ALLORA_WEBSOCKET_ENDPOINT", "wss://allora-rpc.testnet.allora.network:443/websocket"),
            fee_denom=os.getenv("ALLORA_FEE_DENOM", "uallo"),
            fee_minimum_gas_price=float(os.getenv("ALLORA_FEE_MIN_GAS_PRICE", "0.025"))
        )
    
    def to_cosmpy_config(self) -> NetworkConfig:
        return NetworkConfig(
            chain_id=self.chain_id,
            url=self.url,
            fee_minimum_gas_price=self.fee_minimum_gas_price,
            fee_denomination=self.fee_denom,
            staking_denomination=self.fee_denom
        )


