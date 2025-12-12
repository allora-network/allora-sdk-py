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
    def from_env(cls, env_prefix: str | None = None) -> 'AlloraWalletConfig':
        return cls(
            private_key=os.getenv((env_prefix or "") + "PRIVATE_KEY"),
            mnemonic=os.getenv((env_prefix or "") + "MNEMONIC"),
            mnemonic_file=os.getenv((env_prefix or "") + "MNEMONIC_FILE"),
            prefix=os.getenv((env_prefix or "") + "ADDRESS_PREFIX", "allo"),
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
    use_dynamic_gas_price: bool = True
    gas_price_cache_ttl_secs: int = 30
    congestion_aware_fees: bool = False

    @classmethod
    def testnet(
        cls,
        chain_id="allora-testnet-1",
        url="grpc+https://allora-grpc.testnet.allora.network:443",
        websocket_url="wss://allora-rpc.testnet.allora.network/websocket",
        faucet_url="https://faucet.testnet.allora.network",
        fee_denom="uallo",
        fee_minimum_gas_price=10.0,
        use_dynamic_gas_price=True,
        gas_price_cache_ttl_secs=30,
        congestion_aware_fees=False,
    ) -> 'AlloraNetworkConfig':
        return cls(
            chain_id=chain_id,
            url=url,
            websocket_url=websocket_url,
            faucet_url=faucet_url,
            fee_denom=fee_denom,
            fee_minimum_gas_price=fee_minimum_gas_price,
            use_dynamic_gas_price=use_dynamic_gas_price,
            gas_price_cache_ttl_secs=gas_price_cache_ttl_secs,
            congestion_aware_fees=congestion_aware_fees,
        )

    @classmethod
    def mainnet(
        cls,
        chain_id="allora-mainnet-1",
        url="grpc+https://allora-grpc.mainnet.allora.network:443",
        websocket_url="wss://allora-rpc.mainnet.allora.network/websocket",
        fee_denom="uallo",
        fee_minimum_gas_price=250_000_000.0,
        use_dynamic_gas_price=True,
        gas_price_cache_ttl_secs=30,
        congestion_aware_fees=False,
    ) -> 'AlloraNetworkConfig':
        return cls(
            chain_id=chain_id,
            url=url,
            websocket_url=websocket_url,
            fee_denom=fee_denom,
            fee_minimum_gas_price=fee_minimum_gas_price,
            use_dynamic_gas_price=use_dynamic_gas_price,
            gas_price_cache_ttl_secs=gas_price_cache_ttl_secs,
            congestion_aware_fees=congestion_aware_fees,
        )

    @classmethod
    def local(
        cls,
        chain_id="allora-local",
        websocket_url="ws://localhost:26657/websocket",
        fee_denom="uallo",
        fee_minimum_gas_price=0.0,
        use_dynamic_gas_price=False,
        gas_price_cache_ttl_secs=30,
        congestion_aware_fees=False,
        port: int = 26657,
        url: str | None = None,
    ) -> 'AlloraNetworkConfig':
        return cls(
            chain_id=chain_id,
            url=url or f"grpc+http://localhost:{port}",
            websocket_url=websocket_url,
            fee_denom=fee_denom,
            fee_minimum_gas_price=fee_minimum_gas_price,
            use_dynamic_gas_price=use_dynamic_gas_price,
            gas_price_cache_ttl_secs=gas_price_cache_ttl_secs,
            congestion_aware_fees=congestion_aware_fees,
        )

    @classmethod
    def from_env(cls, env_prefix: str | None = None) -> 'AlloraNetworkConfig':
        return cls(
            chain_id=require_env((env_prefix or "") + "CHAIN_ID"),
            url=require_env((env_prefix or "") + "RPC_ENDPOINT"),
            websocket_url=require_env((env_prefix or "") + "WEBSOCKET_ENDPOINT"),
            faucet_url=require_env((env_prefix or "") + "FAUCET_URL"),
            fee_denom=require_env((env_prefix or "") + "FEE_DENOM"),
            fee_minimum_gas_price=float(require_env((env_prefix or "") + "FEE_MIN_GAS_PRICE")),
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
