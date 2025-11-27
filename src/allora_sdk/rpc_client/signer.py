"""
Signer Protocol and implementations for Allora SDK.

This module provides a unified interface for signing operations, supporting:
- LocalWalletSigner: For local credential management (private_key, mnemonic, wallet)
- VaultSigner: For platform-hosted deployments (Forge) with remote signing

Usage:
    # Local signing with private key
    signer = LocalWalletSigner(private_key="deadbeef...")
    signature = signer.sign_digest(digest_bytes)

    # Local signing with mnemonic
    signer = LocalWalletSigner(mnemonic="word1 word2 ...")

    # Remote signing (Forge platform)
    signer = VaultSigner(
        backend_url="http://backend:8080",
        worker_id="uuid",
        auth_token="bootstrap_token"
    )
"""

from typing import Protocol, runtime_checkable, Optional
import logging

logger = logging.getLogger(__name__)


@runtime_checkable
class Signer(Protocol):
    """Abstract interface for signing operations.

    Enables different credential sources to provide a unified signing interface.
    Compatible with existing LocalWallet-based workflows.

    Implementations:
        - LocalWalletSigner: Wraps cosmpy LocalWallet for local signing
        - VaultSigner: Delegates signing to backend for platform-hosted keys
    """

    def sign_digest(self, digest: bytes) -> bytes:
        """Sign a SHA256 digest and return the signature.

        Args:
            digest: 32-byte SHA256 digest to sign

        Returns:
            Signature bytes (typically 64 bytes for secp256k1)
        """
        ...

    @property
    def public_key_hex(self) -> str:
        """Get hex-encoded compressed public key."""
        ...

    @property
    def address(self) -> str:
        """Get bech32 wallet address."""
        ...


class LocalWalletSigner:
    """Signer wrapping cosmpy LocalWallet.

    Supports all existing credential options:
    - private_key: Hex-encoded 32-byte private key
    - mnemonic: BIP39 24-word seed phrase
    - wallet: Pre-initialized LocalWallet instance

    This is the default signer for self-hosted deployments where users
    manage their own credentials.

    Example:
        # From private key
        signer = LocalWalletSigner(private_key="deadbeef...")

        # From mnemonic
        signer = LocalWalletSigner(mnemonic="word1 word2 word3 ...")

        # From existing wallet
        signer = LocalWalletSigner(wallet=my_wallet)

        # Sign a digest
        signature = signer.sign_digest(digest_bytes)
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        mnemonic: Optional[str] = None,
        wallet: Optional["LocalWallet"] = None,
        prefix: str = "allo"
    ):
        """Initialize signer from credentials.

        Args:
            private_key: Hex-encoded 32-byte private key
            mnemonic: BIP39 24-word seed phrase
            wallet: Pre-initialized LocalWallet instance
            prefix: Bech32 address prefix (default: "allo")

        Raises:
            ValueError: If no valid credentials provided
        """
        from cosmpy.aerial.wallet import LocalWallet, PrivateKey

        if wallet is not None:
            self._wallet = wallet
        elif private_key is not None:
            pk = PrivateKey(bytes.fromhex(private_key))
            self._wallet = LocalWallet(pk, prefix=prefix)
        elif mnemonic is not None:
            self._wallet = LocalWallet.from_mnemonic(mnemonic, prefix=prefix)
        else:
            raise ValueError("Must provide private_key, mnemonic, or wallet")

        self._prefix = prefix

    def sign_digest(self, digest: bytes) -> bytes:
        """Sign a SHA256 digest using the local wallet.

        Args:
            digest: 32-byte SHA256 digest to sign

        Returns:
            64-byte secp256k1 signature
        """
        return self._wallet.signer().sign_digest(digest)

    @property
    def public_key_hex(self) -> str:
        """Get hex-encoded compressed public key."""
        return self._wallet.public_key().public_key_hex

    @property
    def address(self) -> str:
        """Get bech32 wallet address."""
        return str(self._wallet.address())

    @property
    def wallet(self) -> "LocalWallet":
        """Access underlying LocalWallet for SDK compatibility.

        This allows existing code that expects a LocalWallet to continue working.
        """
        return self._wallet


class VaultSigner:
    """Remote signer for platform-hosted deployments.

    Delegates signing to a backend service where keys are securely stored.
    Used by Forge platform workers where private keys remain in the vault
    and never leave the secure environment.

    Security benefits:
    - Private keys never exposed to worker pods
    - Centralized key management and rotation
    - Audit logging of all signing operations
    - Keys protected by vault encryption

    Example:
        signer = VaultSigner(
            backend_url="http://backend:8080",
            worker_id="550e8400-e29b-41d4-a716-446655440000",
            auth_token="bootstrap_token_here"
        )

        # Sign a digest (makes HTTP call to backend)
        signature = signer.sign_digest(digest_bytes)
    """

    def __init__(self, backend_url: str, worker_id: str, auth_token: str):
        """Initialize vault signer.

        Args:
            backend_url: Backend API base URL (e.g., "http://backend:8080")
            worker_id: Worker UUID for signing requests
            auth_token: Bootstrap token or auth token for API authentication
        """
        self._backend_url = backend_url.rstrip('/')
        self._worker_id = worker_id
        self._auth_token = auth_token
        self._public_key: Optional[str] = None
        self._address: Optional[str] = None

    def _ensure_identity(self):
        """Lazy-load identity from backend if not cached.

        Fetches public key and address from the backend API.
        These are cached after the first call.
        """
        if self._public_key is None or self._address is None:
            import httpx

            logger.debug(f"Fetching identity for worker {self._worker_id}")
            resp = httpx.get(
                f"{self._backend_url}/api/v1/workers/{self._worker_id}/identity",
                headers={"Authorization": f"Bearer {self._auth_token}"},
                timeout=30.0
            )
            resp.raise_for_status()
            data = resp.json()

            # Support both camelCase (gRPC-gateway) and snake_case
            self._public_key = data.get("publicKey") or data.get("public_key")
            self._address = data.get("address")

            if not self._public_key or not self._address:
                raise RuntimeError("Backend did not return valid identity")

            logger.debug(f"Got identity: address={self._address}")

    def sign_digest(self, digest: bytes) -> bytes:
        """Request signature from backend.

        Makes an HTTP POST to the backend's sign-bundle endpoint.
        The backend retrieves the key from vault and signs the digest.

        Args:
            digest: 32-byte SHA256 digest to sign

        Returns:
            64-byte secp256k1 signature

        Raises:
            httpx.HTTPStatusError: If the backend request fails
            RuntimeError: If the response is invalid
        """
        import httpx

        logger.debug(f"Requesting signature for worker {self._worker_id}")
        resp = httpx.post(
            f"{self._backend_url}/api/v1/workers/{self._worker_id}/sign-bundle",
            headers={"Authorization": f"Bearer {self._auth_token}"},
            json={"digest_hex": digest.hex()},
            timeout=30.0
        )
        resp.raise_for_status()
        data = resp.json()

        # Check for error in response
        if not data.get("success", True):
            error = data.get("error", "Unknown signing error")
            raise RuntimeError(f"Signing failed: {error}")

        # Support both camelCase and snake_case
        signature_hex = (
            data.get("signature") or
            data.get("signatureHex") or
            data.get("signature_hex")
        )

        if not signature_hex:
            raise RuntimeError("Backend did not return signature")

        logger.debug("Signature received successfully")
        return bytes.fromhex(signature_hex)

    @property
    def public_key_hex(self) -> str:
        """Get hex-encoded compressed public key."""
        self._ensure_identity()
        return self._public_key

    @property
    def address(self) -> str:
        """Get bech32 wallet address."""
        self._ensure_identity()
        return self._address


# Type alias for convenience
LocalWallet = "cosmpy.aerial.wallet.LocalWallet"
