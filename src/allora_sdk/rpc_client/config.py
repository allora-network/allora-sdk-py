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
    - wallet: An existing cosmpy Wallet instance (e.g. a LocalWallet for self-managed
      signing, or a RemoteWallet from make_remote_wallet() for Privy-managed signing
      delegated to the Forge backend).

    The address prefix can also be specified (default is "allo").

    fee_granter optionally sets the bech32 address of a fee granter (a master/subsidy
    wallet that has created an on-chain feegrant for this wallet). When set, transactions
    are broadcast with this granter as the fee payer, so the signing wallet needs no ALLO
    of its own — this is the recommended pairing for Privy-delegated (RemoteWallet) signing.
    """
    private_key: Optional[str] = None
    mnemonic: Optional[str] = None
    mnemonic_file: Optional[str] = None
    wallet: Optional[Wallet] = None
    prefix: str = "allo"
    fee_granter: Optional[str] = None
    # Managed (Privy) custody without an explicit wallet: when forge_api_key is set and no
    # wallet/key is provided, the worker provisions a backend-signed wallet bound to its topic
    # (get-or-create) at startup. forge_backend_url defaults to the public Forge backend.
    forge_api_key: Optional[str] = None
    forge_backend_url: Optional[str] = None

    @classmethod
    def from_env(cls, env_prefix: str | None = None) -> 'AlloraWalletConfig':
        p = env_prefix or ""
        prefix = os.getenv(p + "ADDRESS_PREFIX", "allo")
        fee_granter = os.getenv(p + "FEE_GRANTER")

        # Privy-delegated signing: if the Forge env vars are present, build a RemoteWallet
        # so 12-factor deployments can use delegated signing without hand-written wiring.
        # Note: this performs a blocking wallet-info fetch; async callers that need to avoid
        # it can build the wallet via make_remote_wallet(..., public_key_hex=...) directly.
        api_key = os.getenv(p + "FORGE_API_KEY")
        wallet_id = os.getenv(p + "FORGE_SIGNING_WALLET_ID")
        backend_url = os.getenv(p + "FORGE_BACKEND_URL", "https://forge.allora.network")
        if api_key and wallet_id:
            # The early return below never reads PRIVATE_KEY/MNEMONIC/MNEMONIC_FILE, so a
            # stale local-key env var (a common mid-migration state) would be silently
            # ignored and signing would go through Forge with no log. Mirror __post_init__'s
            # "exactly one credential source" guard at the env layer and fail loudly instead.
            conflicting = [
                name
                for name in ("PRIVATE_KEY", "MNEMONIC", "MNEMONIC_FILE")
                if os.getenv(p + name)
            ]
            if conflicting:
                raise ValueError(
                    f"FORGE_API_KEY and FORGE_SIGNING_WALLET_ID are set alongside "
                    f"{conflicting}; choose exactly one signing source"
                )

            from .remote_signer import make_remote_wallet

            wallet = make_remote_wallet(backend_url, api_key, wallet_id, prefix=prefix)
            return cls(wallet=wallet, prefix=prefix, fee_granter=fee_granter)
        if api_key:
            # Same single-source guard as the branch above: without it a stale local-key env
            # var (a common mid-migration state) would be silently ignored while signing
            # switched to managed Forge custody — fail loudly instead of changing the flow.
            conflicting = [
                name
                for name in ("PRIVATE_KEY", "MNEMONIC", "MNEMONIC_FILE")
                if os.getenv(p + name)
            ]
            if conflicting:
                raise ValueError(
                    f"FORGE_API_KEY is set alongside {conflicting}; "
                    f"choose exactly one signing source"
                )
            # Managed custody, no explicit wallet id: defer to the worker, which provisions a
            # wallet bound to its topic (one worker = one topic) at startup.
            return cls(
                forge_api_key=api_key,
                forge_backend_url=backend_url,
                prefix=prefix,
                fee_granter=fee_granter,
            )

        return cls(
            private_key=os.getenv(p + "PRIVATE_KEY"),
            mnemonic=os.getenv(p + "MNEMONIC"),
            mnemonic_file=os.getenv(p + "MNEMONIC_FILE"),
            prefix=prefix,
            fee_granter=fee_granter,
        )

    def __post_init__(self):
        sources = sum(
            x is not None
            for x in (self.private_key, self.mnemonic, self.mnemonic_file, self.wallet)
        )
        # Managed (Privy) custody must be the *sole* credential source. Combined with a local
        # key/wallet, the worker would silently provision and sign with a newly minted remote
        # wallet (wrong worker address / custody path), so reject the ambiguous config up front.
        if self.forge_api_key and sources > 0:
            raise ValueError(
                "forge_api_key (managed custody) cannot be combined with a local wallet "
                "credential (private_key, mnemonic, mnemonic_file, or wallet); choose exactly one"
            )
        # Managed (Privy) custody is a valid "deferred" source: the wallet is provisioned later
        # from forge_api_key + the worker's topic, so no local credential is present here.
        if sources == 0 and self.forge_api_key:
            return
        if sources == 0:
            raise ValueError("No wallet credentials provided")
        if sources > 1:
            # Avoid a silent-precedence footgun (e.g. leaving PRIVATE_KEY set while
            # adding wallet=). Require an unambiguous single credential source.
            raise ValueError(
                "Exactly one of private_key, mnemonic, mnemonic_file, or wallet must be provided"
            )

        if self.wallet is not None:
            # A pre-built wallet fixes its own bech32 prefix at construction time and
            # downstream code uses the wallet directly, so `prefix` would otherwise be a
            # silently-ignored, possibly-misleading value. Align it to the wallet's actual
            # prefix (e.g. a RemoteWallet built with prefix="cosmos").
            # No try/except: only wallet.address() can raise here, and a Wallet whose
            # address() raises is a real bug that should surface, not be swallowed into a
            # silently wrong prefix that fails far downstream in the broadcast path.
            hrp = str(self.wallet.address()).split("1", 1)[0]
            if hrp:
                self.prefix = hrp


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
