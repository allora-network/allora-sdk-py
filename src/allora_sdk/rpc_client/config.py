import os
from dataclasses import dataclass
from typing import Optional
from cosmpy.aerial.config import NetworkConfig
from cosmpy.aerial.wallet import Wallet
from cosmpy.crypto.address import Address


@dataclass
class AlloraWalletConfig:
    """
    Configuration for Allora wallet access.

    Exactly one signing-credential source must be provided:
    - private_key: Hex-encoded private key string.
    - mnemonic: Mnemonic phrase string.
    - mnemonic_file: Path to a file containing the mnemonic phrase.
    - wallet: An existing cosmpy Wallet instance (e.g. a LocalWallet for self-managed
      signing, or a RemoteWallet from make_remote_wallet() for Privy-managed signing
      delegated to the Forge backend).
    - forge_api_key: Managed (Privy) custody without an explicit wallet. When set as the
      sole credential, the worker provisions a backend-signed wallet bound to its topic
      (get-or-create) at startup; it must not be combined with any local credential above.
      forge_backend_url overrides the Forge backend URL (defaults to the public Forge backend).

    The address prefix can also be specified (default is "allo").

    fee_granter optionally sets the bech32 address of a fee granter (a master/subsidy
    wallet that has created an on-chain feegrant for this wallet). When set, transactions
    are broadcast with this granter as the fee payer, so the signing wallet needs no ALLO
    of its own — this is the recommended pairing for Privy-delegated (RemoteWallet) signing.
    Its HRP must match the signing wallet's prefix (validated at construction).
    """
    private_key: Optional[str] = None
    mnemonic: Optional[str] = None
    mnemonic_file: Optional[str] = None
    wallet: Optional[Wallet] = None
    prefix: str = "allo"
    fee_granter: Optional[str] = None
    # Managed (Privy) custody fields — see the class docstring for the resolution rules.
    forge_api_key: Optional[str] = None
    forge_backend_url: Optional[str] = None

    @classmethod
    def from_env(cls, env_prefix: str | None = None) -> 'AlloraWalletConfig':
        """Build an AlloraWalletConfig from environment variables.

        Resolves a single signing-credential source in this precedence order:

        1. Privy-delegated signing — ``FORGE_API_KEY`` + ``FORGE_SIGNING_WALLET_ID``: builds a
           RemoteWallet. This performs a blocking ``GET /api/v1/signing-wallets/{id}`` to fetch the
           wallet's pubkey/address; async callers that must avoid it can build the wallet via
           ``make_remote_wallet(..., public_key_hex=...)`` directly.
        2. Deferred managed custody — ``FORGE_API_KEY`` only: returns a config the worker
           provisions into a topic-bound wallet at startup (no wallet built, no network call here).
        3. Local key — ``PRIVATE_KEY`` / ``MNEMONIC`` / ``MNEMONIC_FILE``.

        ``FORGE_BACKEND_URL`` (default ``https://forge.allora.network``), ``ADDRESS_PREFIX``
        (default ``allo``), and ``FEE_GRANTER`` are read in all modes.

        Args:
            env_prefix: Optional prefix applied to every variable name (e.g. ``"ALLORA_"``).

        Returns:
            A validated AlloraWalletConfig.

        Raises:
            ValueError: If ``FORGE_API_KEY`` (modes 1-2) is set alongside any local key env var
                (the signing flow must be unambiguous), or if no credential source is present.
        """
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
            self._validate_fee_granter()
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
            # A pre-built wallet fixes its own bech32 prefix at construction time, so a
            # caller-supplied `prefix` is either redundant or contradictory. Derive the
            # wallet's actual HRP and reconcile it with `prefix`:
            #   - prefix left at the default ("allo"): the caller expressed no opinion, so
            #     silently align it to the wallet's HRP (e.g. a RemoteWallet built with
            #     prefix="cosmos").
            #   - prefix explicitly set to a value that disagrees: raise rather than
            #     silently overwrite the caller's stated intent. A silent overwrite hides a
            #     real misconfiguration — e.g. a fee_granter the caller sized to the prefix
            #     they passed would then fail the HRP check below for a confusing reason.
            # No try/except: only wallet.address() can raise here, and a Wallet whose
            # address() raises is a real bug that should surface, not be swallowed into a
            # silently wrong prefix that fails far downstream in the broadcast path.
            hrp = str(self.wallet.address()).rsplit("1", 1)[0]
            if hrp and hrp != self.prefix:
                # "allo" is the dataclass default; any other value is an explicit choice.
                if self.prefix != "allo":
                    raise ValueError(
                        f"prefix={self.prefix!r} disagrees with the provided wallet's HRP "
                        f"{hrp!r}; omit prefix or pass prefix={hrp!r}"
                    )
                self.prefix = hrp

        # Validate fee_granter after the wallet's prefix has been realigned, so the HRP check
        # compares against the signing wallet's actual prefix.
        self._validate_fee_granter()

    def _validate_fee_granter(self) -> None:
        """Validate fee_granter eagerly so a bad granter fails at startup, not per-broadcast.

        Parsing it here (rather than for the first time deep inside
        ``TxManager._build_and_broadcast``) surfaces a typo at config time and rejects a
        cross-HRP pairing — e.g. a ``cosmos1`` granter with an ``allo1`` signing wallet — which
        the chain would otherwise reject on-chain with an opaque "feegrant not found" error,
        because the chain-side lookup cannot match a different-HRP granter to the grantee.
        """
        if self.fee_granter is None:
            return
        try:
            parsed = Address(self.fee_granter)
        except Exception as e:
            raise ValueError(f"invalid fee_granter address {self.fee_granter!r}: {e}") from e
        # cosmpy's Address(str) validates only the bech32 checksum, not the decoded payload
        # length, so a checksum-valid string with the wrong number of data bytes slips
        # through. Cosmos account addresses are 20 bytes (ripemd160(sha256(pubkey))); reject
        # any other length here rather than letting it fail opaquely on-chain at broadcast.
        if len(bytes(parsed)) != 20:
            raise ValueError(
                f"invalid fee_granter address {self.fee_granter!r}: expected a 20-byte "
                f"account address, got {len(bytes(parsed))} bytes"
            )
        # bech32's separator is the LAST '1' (BIP 173); everything before it is the HRP.
        hrp = self.fee_granter.rsplit("1", 1)[0]
        if hrp != self.prefix:
            raise ValueError(
                f"fee_granter HRP {hrp!r} does not match the signing wallet prefix "
                f"{self.prefix!r}; the chain cannot match a cross-HRP feegrant"
            )


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
