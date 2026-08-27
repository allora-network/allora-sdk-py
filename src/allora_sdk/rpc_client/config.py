import logging
import os
from dataclasses import dataclass
from typing import Optional
from cosmpy.aerial.config import NetworkConfig
from cosmpy.aerial.wallet import Wallet

logger = logging.getLogger("allora_sdk")


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
    # Websocket subscription tuning (previously only settable by constructing
    # AlloraWebsocketSubscriber directly). event_recv_timeout_secs bounds a single
    # recv(); max_event_silence_secs gates the deaf-subscription watchdog (force a
    # reconnect+resubscribe after this long with no message). Defaults match the
    # subscriber's own, so behavior is unchanged unless overridden. Raise
    # max_event_silence_secs for subscriptions that are legitimately idle longer.
    event_recv_timeout_secs: float = 30.0
    max_event_silence_secs: float = 60.0
    # Subscribe to NewBlock alongside the caller's queries purely to keep
    # traffic on the wire. Without it, a subscription filtered server-side to
    # one topic is silent between events, and the watchdog above cannot tell a
    # quiet subscription from a deaf connection -- so it reconnects a healthy
    # socket, and an event arriving mid-reconnect is lost.
    websocket_heartbeat: bool = True

    def __post_init__(self):
        # Same invariants the subscriber enforces — fail fast at config
        # construction rather than when the websocket loop starts.
        if self.event_recv_timeout_secs <= 0:
            raise ValueError(
                f"event_recv_timeout_secs must be > 0, got {self.event_recv_timeout_secs!r}"
            )
        if self.max_event_silence_secs <= self.event_recv_timeout_secs:
            raise ValueError(
                "max_event_silence_secs must be greater than event_recv_timeout_secs "
                f"({self.max_event_silence_secs!r} <= {self.event_recv_timeout_secs!r})"
            )

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
        event_recv_timeout_secs=30.0,
        max_event_silence_secs=60.0,
        websocket_heartbeat=True,
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
            event_recv_timeout_secs=event_recv_timeout_secs,
            max_event_silence_secs=max_event_silence_secs,
            websocket_heartbeat=websocket_heartbeat,
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
        event_recv_timeout_secs=30.0,
        max_event_silence_secs=60.0,
        websocket_heartbeat=True,
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
            event_recv_timeout_secs=event_recv_timeout_secs,
            max_event_silence_secs=max_event_silence_secs,
            websocket_heartbeat=websocket_heartbeat,
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
        event_recv_timeout_secs=30.0,
        max_event_silence_secs=60.0,
        websocket_heartbeat=True,
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
            event_recv_timeout_secs=event_recv_timeout_secs,
            max_event_silence_secs=max_event_silence_secs,
            websocket_heartbeat=websocket_heartbeat,
        )

    @classmethod
    def from_env(cls, env_prefix: str | None = None) -> 'AlloraNetworkConfig':
        return cls(
            chain_id=require_env((env_prefix or "") + "CHAIN_ID"),
            url=require_env((env_prefix or "") + "RPC_ENDPOINT"),
            websocket_url=require_env((env_prefix or "") + "WEBSOCKET_ENDPOINT"),
            faucet_url=require_env((env_prefix or "") + "FAUCET_URL"),
            fee_denom=require_env((env_prefix or "") + "FEE_DENOM"),
            fee_minimum_gas_price=_env_float((env_prefix or "") + "FEE_MIN_GAS_PRICE", required=True),
            event_recv_timeout_secs=_env_float((env_prefix or "") + "EVENT_RECV_TIMEOUT_SECS", 30.0),
            max_event_silence_secs=_env_float((env_prefix or "") + "MAX_EVENT_SILENCE_SECS", 60.0),
            websocket_heartbeat=_env_bool((env_prefix or "") + "WEBSOCKET_HEARTBEAT", True),
        )

    def to_cosmpy_config(self) -> NetworkConfig:
        return NetworkConfig(
            chain_id=self.chain_id,
            url=self.url,
            fee_minimum_gas_price=self.fee_minimum_gas_price,
            fee_denomination=self.fee_denom,
            staking_denomination=self.fee_denom
        )


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean env var, treating blank as unset.

    Same reasoning as _env_float: k8s renders `value: ""` for unconfigured
    knobs, and a blank value must mean "use the default" rather than False.
    """
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_float(name: str, default: float | None = None, required: bool = False) -> float:
    """Read a float env var, treating blank as unset and naming the variable on error.

    k8s manifests render `value: ""` for unconfigured knobs, so a bare float("")
    would both crash on an empty value and hide which variable was at fault.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        if required or default is None:
            raise RuntimeError(f"environment variable {name} is required")
        return default
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"environment variable {name} must be a number, got {raw!r}") from None


def require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"environment variable {name} is required")
    return value
