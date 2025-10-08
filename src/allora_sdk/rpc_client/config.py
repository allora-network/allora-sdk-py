import os
from dataclasses import dataclass
from typing import Optional
from cosmpy.aerial.config import NetworkConfig
from cosmpy.aerial.wallet import LocalWallet


@dataclass
class AlloraWalletConfig:
    """
    Configuration for Allora wallet access.

    At least one of the following must be provided:
    - private_key: Hex-encoded private key string.
    - mnemonic: Mnemonic phrase string.
    - mnemonic_file: Path to a file containing the mnemonic phrase.
    - wallet: An existing LocalWallet instance.

    The address prefix can also be specified (default is "allo").
    """
    private_key: Optional[str] = None
    mnemonic: Optional[str] = None
    mnemonic_file: Optional[str] = None
    wallet: Optional[LocalWallet] = None
    prefix: str = "allo"

    @classmethod
    def from_env(cls) -> 'AlloraWalletConfig':
        return cls(
            private_key=os.getenv("PRIVATE_KEY"),
            mnemonic=os.getenv("MNEMONIC"),
            mnemonic_file=os.getenv("MNEMONIC_FILE"),
            prefix=os.getenv("ADDRESS_PREFIX", "allo"),
        )

    def __post_init__(self):
        if (
            self.private_key is None and
            self.mnemonic is None and
            self.mnemonic_file is None and
            self.wallet is None
        ):
            raise ValueError("No wallet credentials provided")


@dataclass
class AlloraNetworkConfig:
    """Configuration for Allora blockchain networks."""
    
    chain_id: str
    url: str
    websocket_url: Optional[str] = None
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
            fee_minimum_gas_price=10.0,
        )

    @classmethod
    def mainnet(cls) -> 'AlloraNetworkConfig':
        return cls(
            chain_id="allora-mainnet-1",
            url="grpc+https://allora-grpc.mainnet.allora.network:443",
            websocket_url="wss://allora-rpc.mainnet.allora.network/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=10.0,
        )

    @classmethod
    def local(cls, port: int = 26657) -> 'AlloraNetworkConfig':
        return cls(
            chain_id="allora-local",
            url=f"grpc+http://localhost:{port}",
            websocket_url=f"ws://localhost:26657/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=0.0,
        )

    @classmethod
    def from_env(cls) -> 'AlloraNetworkConfig':
        return cls(
            chain_id=require_env("CHAIN_ID"),
            url=require_env("RPC_ENDPOINT"),
            websocket_url=require_env("WEBSOCKET_ENDPOINT"),
            faucet_url=require_env("FAUCET_URL"),
            fee_denom=require_env("FEE_DENOM"),
            fee_minimum_gas_price=float(require_env("FEE_MIN_GAS_PRICE")),
        )
    
    def to_cosmpy_config(self) -> NetworkConfig:
        return NetworkConfig(
            chain_id=self.chain_id,
            url=self.url,
            fee_minimum_gas_price=self.fee_minimum_gas_price,
            fee_denomination=self.fee_denom,
            staking_denomination=self.fee_denom
        )


def require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"environment variable {name} is required")
    return value
