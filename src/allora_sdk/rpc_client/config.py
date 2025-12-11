import os
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING
from cosmpy.aerial.config import NetworkConfig
from cosmpy.aerial.wallet import LocalWallet

if TYPE_CHECKING:
    from .signer import Signer


@dataclass
class AlloraWalletConfig:
    """
    Configuration for Allora wallet access.

    At least one of the following must be provided:
    - private_key: Hex-encoded private key string.
    - mnemonic: Mnemonic phrase string.
    - mnemonic_file: Path to a file containing the mnemonic phrase.
    - wallet: An existing LocalWallet instance.
    - signer: A custom Signer implementation (for advanced use cases like VaultSigner).

    The address prefix can also be specified (default is "allo").

    Example usage:
        # Using private key
        config = AlloraWalletConfig(private_key="deadbeef...")

        # Using mnemonic
        config = AlloraWalletConfig(mnemonic="word1 word2 ...")

        # Using custom signer (e.g., VaultSigner for Forge platform)
        from allora_sdk import VaultSigner
        signer = VaultSigner(backend_url="...", worker_id="...", auth_token="...")
        config = AlloraWalletConfig(signer=signer)
    """
    private_key: Optional[str] = None
    mnemonic: Optional[str] = None
    mnemonic_file: Optional[str] = None
    wallet: Optional[LocalWallet] = None
    prefix: str = "allo"

    # Custom signer for advanced use cases (e.g., VaultSigner for Forge platform)
    signer: Optional["Signer"] = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> 'AlloraWalletConfig':
        """Create config from environment variables.

        Supported environment variables:
        - PRIVATE_KEY: Hex-encoded private key
        - MNEMONIC: BIP39 mnemonic phrase
        - MNEMONIC_FILE: Path to file containing mnemonic
        - ADDRESS_PREFIX: Bech32 prefix (default: "allo")
        """
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
            self.wallet is None and
            self.signer is None
        ):
            raise ValueError("No wallet credentials provided")

    def get_signer(self) -> "Signer":
        """Get a Signer from the configured credential source.

        Priority: custom signer > wallet > private_key > mnemonic > mnemonic_file

        Returns:
            Signer instance for signing operations

        Raises:
            ValueError: If no valid credentials are configured
        """
        from .signer import LocalWalletSigner

        # Custom signer takes precedence
        if self.signer is not None:
            return self.signer

        # Existing LocalWallet instance
        if self.wallet is not None:
            return LocalWalletSigner(wallet=self.wallet, prefix=self.prefix)

        # Private key
        if self.private_key is not None:
            return LocalWalletSigner(private_key=self.private_key, prefix=self.prefix)

        # Mnemonic (direct or from file)
        mnemonic = self.mnemonic
        if mnemonic is None and self.mnemonic_file is not None:
            with open(self.mnemonic_file, 'r') as f:
                mnemonic = f.read().strip()

        if mnemonic is not None:
            return LocalWalletSigner(mnemonic=mnemonic, prefix=self.prefix)

        raise ValueError("No valid credentials configured")


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
    def testnet(cls) -> 'AlloraNetworkConfig':
        return cls(
            chain_id="allora-testnet-1",
            url="grpc+https://allora-grpc.testnet.allora.network:443",
            websocket_url="wss://allora-rpc.testnet.allora.network/websocket",
            faucet_url="https://faucet.testnet.allora.network",
            fee_denom="uallo",
            fee_minimum_gas_price=10.0,
            use_dynamic_gas_price=True,
            gas_price_cache_ttl_secs=30,
            congestion_aware_fees=False,
        )

    @classmethod
    def mainnet(cls) -> 'AlloraNetworkConfig':
        return cls(
            chain_id="allora-mainnet-1",
            url="grpc+https://allora-grpc.mainnet.allora.network:443",
            websocket_url="wss://allora-rpc.mainnet.allora.network/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=250_000_000.0,
            use_dynamic_gas_price=True,
            gas_price_cache_ttl_secs=30,
            congestion_aware_fees=False,
        )

    @classmethod
    def local(cls, port: int = 26657) -> 'AlloraNetworkConfig':
        return cls(
            chain_id="allora-local",
            url=f"grpc+http://localhost:{port}",
            websocket_url=f"ws://localhost:26657/websocket",
            fee_denom="uallo",
            fee_minimum_gas_price=0.0,
            use_dynamic_gas_price=False,
            gas_price_cache_ttl_secs=30,
            congestion_aware_fees=False,
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
