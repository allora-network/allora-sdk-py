import os
from dataclasses import dataclass
from typing import Optional
from cosmpy.aerial.config import NetworkConfig
from cosmpy.aerial.wallet import Wallet


@dataclass
class AlloraWalletConfig:
    """
    Configuration for Allora wallet access.

    At least one of the following must be provided:
    - private_key: Hex-encoded private key string.
    - mnemonic: Mnemonic phrase string.
    - mnemonic_file: Path to a file containing the mnemonic phrase.
    - wallet: An existing wallet instance. This is the abstract cosmpy ``Wallet``
      (not only ``LocalWallet``), so a custodial/remote-signing implementation
      (e.g. a Privy-backed wallet that never materializes the private key) can be
      injected here. When set, it takes precedence and ``private_key`` /
      ``mnemonic`` / ``mnemonic_file`` are IGNORED (there is no fallback chain —
      the injected wallet is used as-is).

    The address prefix can also be specified (default is "allo").
    """
    private_key: Optional[str] = None
    mnemonic: Optional[str] = None
    mnemonic_file: Optional[str] = None
    wallet: Optional[Wallet] = None
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
    dynamic_gas_price_default_multiplier: float = 3.0
    gas_price_cache_ttl_secs: int = 30
    congestion_aware_fees: bool = False
    query_timeout_secs: int = 10
    grpc_max_connection_age_secs: int = 1800
    grpc_drain_window_secs: int = 5
    # Multiplier applied to the simulated gas estimate on the first broadcast
    # attempt. Default 1.0 preserves existing behavior (broadcast at exactly the
    # estimate). Since execution can consume slightly more gas than simulation
    # reports (state-dependent writes), broadcasting at exactly the estimate
    # risks out-of-gas — with no room to recover if the caller disables retries;
    # set this above 1.0 (e.g. 1.4, standard cosmos headroom) to add margin.
    gas_adjustment: float = 1.0

    @classmethod
    def testnet(
        cls,
        chain_id="allora-testnet-1",
        url="grpc+https://allora-grpc.testnet.allora.network:443",
        websocket_url="wss://allora-rpc.testnet.allora.network/websocket",
        faucet_url="https://faucet.testnet.allora.run",
        fee_denom="uallo",
        fee_minimum_gas_price=10.0,
        use_dynamic_gas_price=True,
        dynamic_gas_price_default_multiplier=3.0,
        gas_price_cache_ttl_secs=30,
        congestion_aware_fees=False,
        grpc_max_connection_age_secs=1800,
        grpc_drain_window_secs=5,
    ) -> 'AlloraNetworkConfig':
        return cls(
            chain_id=chain_id,
            url=url,
            websocket_url=websocket_url,
            faucet_url=faucet_url,
            fee_denom=fee_denom,
            fee_minimum_gas_price=fee_minimum_gas_price,
            use_dynamic_gas_price=use_dynamic_gas_price,
            dynamic_gas_price_default_multiplier=dynamic_gas_price_default_multiplier,
            gas_price_cache_ttl_secs=gas_price_cache_ttl_secs,
            congestion_aware_fees=congestion_aware_fees,
            grpc_max_connection_age_secs=grpc_max_connection_age_secs,
            grpc_drain_window_secs=grpc_drain_window_secs,
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
        dynamic_gas_price_default_multiplier=3.0,
        gas_price_cache_ttl_secs=30,
        congestion_aware_fees=False,
        grpc_max_connection_age_secs=1800,
        grpc_drain_window_secs=5,
    ) -> 'AlloraNetworkConfig':
        return cls(
            chain_id=chain_id,
            url=url,
            websocket_url=websocket_url,
            fee_denom=fee_denom,
            fee_minimum_gas_price=fee_minimum_gas_price,
            use_dynamic_gas_price=use_dynamic_gas_price,
            dynamic_gas_price_default_multiplier=dynamic_gas_price_default_multiplier,
            gas_price_cache_ttl_secs=gas_price_cache_ttl_secs,
            congestion_aware_fees=congestion_aware_fees,
            grpc_max_connection_age_secs=grpc_max_connection_age_secs,
            grpc_drain_window_secs=grpc_drain_window_secs,
        )

    @classmethod
    def local(
        cls,
        chain_id="localnet",
        websocket_url="ws://localhost:26657/websocket",
        fee_denom="uallo",
        fee_minimum_gas_price=1.0,
        use_dynamic_gas_price=False,
        dynamic_gas_price_default_multiplier=3.0,
        gas_price_cache_ttl_secs=30,
        congestion_aware_fees=False,
        query_timeout_secs=30,
        grpc_max_connection_age_secs=1800,
        grpc_drain_window_secs=5,
        port: int = 9090,
        url: str | None = None,
    ) -> 'AlloraNetworkConfig':
        return cls(
            chain_id=chain_id,
            url=url or f"grpc+http://localhost:{port}",
            websocket_url=websocket_url,
            fee_denom=fee_denom,
            fee_minimum_gas_price=fee_minimum_gas_price,
            use_dynamic_gas_price=use_dynamic_gas_price,
            dynamic_gas_price_default_multiplier=dynamic_gas_price_default_multiplier,
            gas_price_cache_ttl_secs=gas_price_cache_ttl_secs,
            congestion_aware_fees=congestion_aware_fees,
            query_timeout_secs=query_timeout_secs,
            grpc_max_connection_age_secs=grpc_max_connection_age_secs,
            grpc_drain_window_secs=grpc_drain_window_secs,
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
