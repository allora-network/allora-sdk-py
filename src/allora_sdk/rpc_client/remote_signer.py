"""
Privy-managed (delegated) signing for the Allora SDK.

These types let a worker sign without holding a private key: signing is delegated to
the Forge backend, which signs with a Privy server wallet. They implement cosmpy's
``Signer`` and ``Wallet`` interfaces, so they drop straight into the existing tx and
bundle signing paths (``TxManager`` and ``EmissionsClient``) with no other changes.

Usage::

    from allora_sdk import make_remote_wallet, AlloraWalletConfig, AlloraRPCClient

    wallet = make_remote_wallet(
        backend_url="https://forge.allora.network",
        api_key="forge_sk_...",
        wallet_id="<signing-wallet-uuid>",
    )
    client = AlloraRPCClient(wallet=AlloraWalletConfig(wallet=wallet), ...)
"""

import json
import urllib.parse
from typing import Any, Optional, TypeVar

import requests
from pydantic import BaseModel, ValidationError
from cosmpy.aerial.wallet import Wallet
from cosmpy.crypto.address import Address
from cosmpy.crypto.interface import Signer
from cosmpy.crypto.keypairs import PublicKey

API_KEY_HEADER = "X-Forge-API-Key"
DEFAULT_TIMEOUT = 30.0


class RemoteSignerError(Exception):
    """Base exception for Privy-delegated (remote) signing errors."""


class ForgeBackendError(RemoteSignerError):
    """The Forge backend returned an HTTP error, was unreachable, or sent a
    malformed/unexpected response."""


class WalletConfigError(RemoteSignerError):
    """The remote wallet is misconfigured (e.g. the backend-reported address does
    not match the public key, or required fields are inconsistent)."""


class SigningWalletInfo(BaseModel):
    """Non-secret view of a Forge signing wallet, as returned by the backend.

    The wire shape is a cross-repo HTTP contract shared with allora-sdk-go,
    allora-sdk-ts, and forge-v2.
    """

    id: str
    address: str
    pubkey: str  # hex-encoded 33-byte compressed secp256k1 public key


class SignResult(BaseModel):
    """A signature produced by the Forge backend."""

    signature: str  # hex-encoded 64-byte canonical (low-S) secp256k1 signature
    pubkey: Optional[str] = None


_M = TypeVar("_M", bound=BaseModel)


def _validate(model: type[_M], raw: dict[str, Any], what: str) -> _M:
    try:
        return model.model_validate(raw)
    except ValidationError as e:
        raise ForgeBackendError(f"unexpected forge {what} response: {e}") from e


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _validate_backend_url(url: str) -> None:
    """Require https:// for the Forge backend so the long-lived API key is never sent
    in cleartext. Plain http:// is permitted only for loopback (local development)."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS:
        return
    raise ValueError(
        f"backend_url must use https:// (got {parsed.scheme or 'no'} scheme); "
        "http:// is only allowed for loopback hosts in local development"
    )


class ForgeBackendClient:
    """HTTP transport for the Forge signing-wallet API.

    Owns a single :class:`requests.Session` so connections to the backend are reused
    across wallet-info and signing calls. Inject a custom ``session`` to install a CA
    bundle, retry policy, or a stub transport in tests. All errors surface as
    :class:`ForgeBackendError`.
    """

    def __init__(
        self,
        backend_url: str,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT,
        session: Optional[requests.Session] = None,
    ):
        _validate_backend_url(backend_url)
        self._base = backend_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._session = session if session is not None else requests.Session()

    def get_wallet_info(self, wallet_id: str) -> SigningWalletInfo:
        """Fetch a signing wallet's public, non-secret info (id, address, pubkey)."""
        raw = self._request("GET", f"/api/v1/signing-wallets/{wallet_id}")
        return _validate(SigningWalletInfo, raw, "wallet-info")

    def sign(self, wallet_id: str, payload: bytes, prehashed: bool) -> SignResult:
        """Delegate signing of ``payload`` to the backend.

        When ``prehashed`` is False the backend SHA-256 hashes the payload (Cosmos
        SignDoc); when True it signs the 32-byte digest as-is.
        """
        body = json.dumps({"payload": payload.hex(), "prehashed": prehashed})
        raw = self._request("POST", f"/api/v1/signing-wallets/{wallet_id}/sign", body)
        return _validate(SignResult, raw, "sign")

    def _request(self, method: str, path: str, body: Optional[str] = None) -> dict[str, Any]:
        headers = {API_KEY_HEADER: self._api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            # Never follow redirects: requests re-sends the X-Forge-API-Key header on
            # cross-host 3xx, so a redirecting/compromised backend could exfiltrate the
            # key (which authorizes delegated signing). Treat any 3xx as an error below.
            resp = self._session.request(
                method,
                self._base + path,
                data=body,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            raise ForgeBackendError(f"failed to reach forge backend: {e}") from e

        if not (200 <= resp.status_code < 300):
            raise ForgeBackendError(f"forge backend returned {resp.status_code}: {resp.text}")
        return resp.json()


class RemoteSigner(Signer):
    """A cosmpy Signer that delegates signing to the Forge backend.

    ``sign`` is used for Cosmos transaction signatures (the backend SHA-256 hashes the
    SignDoc bytes before signing); ``sign_digest`` is used for application-level bundle
    signatures, where the 32-byte digest is signed as-is. Both return a 64-byte
    canonical (low-S) secp256k1 signature.

    The private key never leaves Privy/the backend.
    """

    def __init__(self, client: ForgeBackendClient, wallet_id: str):
        self._client = client
        self._wallet_id = wallet_id

    def sign(self, message: bytes, deterministic: bool = False, canonicalise: bool = True) -> bytes:
        # The backend hashes the message (SHA-256) before signing, matching cosmpy's
        # local PrivateKey.sign semantics over the SignDoc bytes.
        return self._remote_sign(message, prehashed=False)

    def sign_digest(self, digest: bytes, deterministic: bool = False, canonicalise: bool = True) -> bytes:
        # The digest is already hashed; the backend signs it directly.
        return self._remote_sign(digest, prehashed=True)

    def _remote_sign(self, payload: bytes, prehashed: bool) -> bytes:
        result = self._client.sign(self._wallet_id, payload, prehashed)
        if not result.signature:
            raise ForgeBackendError("forge sign response missing 'signature'")
        return bytes.fromhex(result.signature)


class RemoteWallet(Wallet):
    """A cosmpy Wallet backed by a Forge signing wallet (Privy server wallet).

    The public key and address are fetched from the backend on construction (or supplied
    directly) so the wallet can seal/simulate transactions before it has ever transacted
    on-chain. ``signer()`` returns a :class:`RemoteSigner`.
    """

    def __init__(
        self,
        backend_url: str,
        api_key: str,
        wallet_id: str,
        prefix: str = "allo",
        timeout: float = DEFAULT_TIMEOUT,
        public_key_hex: Optional[str] = None,
        client: Optional[ForgeBackendClient] = None,
    ):
        self._wallet_id = wallet_id
        self._prefix = prefix
        self._client = client if client is not None else ForgeBackendClient(backend_url, api_key, timeout)
        self._signer = RemoteSigner(self._client, wallet_id)

        reported_address: Optional[str] = None
        if public_key_hex is None:
            info = self._client.get_wallet_info(wallet_id)
            public_key_hex = info.pubkey
            reported_address = info.address
            if not public_key_hex:
                raise ForgeBackendError("forge wallet-info response missing 'pubkey'")

        self._public_key = PublicKey(bytes.fromhex(public_key_hex))

        # Cross-check the backend's reported address against the pubkey-derived one so a
        # misconfigured wallet fails here rather than producing rejected transactions.
        derived = str(Address(self._public_key, self._prefix))
        if reported_address and reported_address != derived:
            raise WalletConfigError(
                f"backend address {reported_address} does not match pubkey-derived address {derived}"
            )

    def address(self) -> Address:
        return Address(self._public_key, self._prefix)

    def public_key(self) -> PublicKey:
        return self._public_key

    def signer(self) -> Signer:
        return self._signer


def make_remote_wallet(
    backend_url: str,
    api_key: str,
    wallet_id: str,
    prefix: str = "allo",
    timeout: float = DEFAULT_TIMEOUT,
    client: Optional[ForgeBackendClient] = None,
) -> RemoteWallet:
    """Construct a backend-backed wallet for the Privy-managed signing path.

    Pass the result to ``AlloraWalletConfig(wallet=...)`` (or ``AlloraWorker`` via its
    wallet config) to sign through the Forge backend instead of a local key. Inject a
    custom ``client`` (e.g. with a tuned :class:`requests.Session`) to control the HTTP
    transport.
    """
    return RemoteWallet(
        backend_url, api_key, wallet_id, prefix=prefix, timeout=timeout, client=client
    )
