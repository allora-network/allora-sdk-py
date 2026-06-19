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
# Legitimate responses (hex signature + pubkey, wallet-info object) are well under 1 KiB.
# Cap reads so a misbehaving or hostile backend cannot drive the worker to OOM.
MAX_RESPONSE_BYTES = 64 * 1024


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
        wid = urllib.parse.quote(wallet_id, safe="")
        raw = self._request("GET", f"/api/v1/signing-wallets/{wid}")
        return _validate(SigningWalletInfo, raw, "wallet-info")

    def sign(self, wallet_id: str, payload: bytes, prehashed: bool) -> SignResult:
        """Delegate signing of ``payload`` to the backend.

        When ``prehashed`` is False the backend SHA-256 hashes the payload (Cosmos
        SignDoc); when True it signs the 32-byte digest as-is.
        """
        wid = urllib.parse.quote(wallet_id, safe="")
        body = json.dumps({"payload": payload.hex(), "prehashed": prehashed})
        raw = self._request("POST", f"/api/v1/signing-wallets/{wid}/sign", body)
        return _validate(SignResult, raw, "sign")

    def _request(self, method: str, path: str, body: Optional[str] = None) -> dict[str, Any]:
        headers = {API_KEY_HEADER: self._api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            # Never follow redirects: requests re-sends the X-Forge-API-Key header on
            # cross-host 3xx, so a redirecting/compromised backend could exfiltrate the
            # key (which authorizes delegated signing). Treat any 3xx as an error below.
            # stream=True so the body is read with an explicit size cap (see below).
            resp = self._session.request(
                method,
                self._base + path,
                data=body,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as e:
            raise ForgeBackendError(f"failed to reach forge backend: {e}") from e

        with resp:
            raw = resp.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ForgeBackendError(
                f"forge backend response exceeded {MAX_RESPONSE_BYTES} bytes"
            )

        if not (200 <= resp.status_code < 300):
            # Truncate: backend 4xx bodies can reflect request fields / wallet ids, and
            # this message bubbles up to operator logs.
            detail = raw.decode(errors="replace")[:512]
            raise ForgeBackendError(f"forge backend returned {resp.status_code}: {detail}")

        try:
            parsed = json.loads(raw.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            snippet = raw.decode(errors="replace")[:256]
            raise ForgeBackendError(
                f"forge backend returned non-JSON response: {snippet!r}"
            ) from e
        if not isinstance(parsed, dict):
            raise ForgeBackendError(
                f"forge backend returned non-object JSON ({type(parsed).__name__})"
            )
        return parsed


class RemoteSigner(Signer):
    """A cosmpy Signer that delegates signing to the Forge backend.

    ``sign`` is used for Cosmos transaction signatures (the backend SHA-256 hashes the
    SignDoc bytes before signing); ``sign_digest`` is used for application-level bundle
    signatures, where the 32-byte digest is signed as-is. Both return a 64-byte
    canonical (low-S) secp256k1 signature.

    The private key never leaves Privy/the backend.
    """

    def __init__(
        self,
        client: ForgeBackendClient,
        wallet_id: str,
        public_key: PublicKey,
    ):
        self._client = client
        self._wallet_id = wallet_id
        # The wallet pubkey verifies every returned signature locally so a backend bug or
        # MITM cannot make the worker broadcast garbage. Required (not optional) so a signer
        # constructed directly can never silently skip verification; RemoteWallet supplies it.
        self._public_key = public_key

    def sign(self, message: bytes, deterministic: bool = False, canonicalise: bool = True) -> bytes:
        # The backend hashes the message (SHA-256) before signing, matching cosmpy's
        # local PrivateKey.sign semantics over the SignDoc bytes.
        self._check_canonicalise(canonicalise)
        return self._remote_sign(message, prehashed=False)

    def sign_digest(self, digest: bytes, deterministic: bool = False, canonicalise: bool = True) -> bytes:
        # The digest is already hashed; the backend signs it directly.
        self._check_canonicalise(canonicalise)
        return self._remote_sign(digest, prehashed=True)

    @staticmethod
    def _check_canonicalise(canonicalise: bool) -> None:
        # The backend always returns a canonical (low-S) signature; we cannot honor a
        # request for a non-canonical one, so reject it rather than silently lie about
        # the encoding. (deterministic is always RFC 6979 on the backend, which is a
        # valid signature regardless of the requested value, so it is not rejected.)
        if not canonicalise:
            raise ValueError(
                "RemoteSigner only produces canonical (low-S) signatures; "
                "canonicalise=False is not supported"
            )

    def _remote_sign(self, payload: bytes, prehashed: bool) -> bytes:
        if not payload:
            # The backend binds `payload` with gin's `binding:"required"`, which treats
            # an empty string as missing and returns a 400; fail locally with a clear error.
            raise ValueError("cannot sign an empty payload")
        result = self._client.sign(self._wallet_id, payload, prehashed)
        if not result.signature:
            raise ForgeBackendError("forge sign response missing 'signature'")
        try:
            sig = bytes.fromhex(result.signature)
        except ValueError as e:
            raise ForgeBackendError(f"forge sign response 'signature' is not valid hex: {e}") from e
        if len(sig) != 64:
            # Cosmos secp256k1 signatures are exactly 64 raw bytes (r || s). A 65-byte
            # recoverable form or DER encoding would be rejected on-chain with an opaque
            # error, so fail fast here instead.
            raise ForgeBackendError(
                f"forge sign response signature has wrong length {len(sig)} (expected 64)"
            )
        self._verify(payload, sig, prehashed, result.pubkey)
        return sig

    def _verify(self, payload: bytes, sig: bytes, prehashed: bool, response_pubkey: Optional[str]) -> None:
        """Verify the backend's signature against the pinned wallet public key."""
        # Compare decoded bytes, not hex strings: bytes.hex() is lowercase but a future
        # backend / proxy could return uppercase hex for an otherwise-valid signature.
        # (Go decodes both sides to bytes; allora-sdk-ts lower-cases defensively.)
        expected = self._public_key.public_key_bytes
        if response_pubkey:
            try:
                resp_bytes = bytes.fromhex(response_pubkey)
            except ValueError as e:
                raise ForgeBackendError(
                    f"forge sign response pubkey is not valid hex: {e}"
                ) from e
            if resp_bytes != expected:
                raise WalletConfigError(
                    "forge sign response pubkey does not match the wallet public key"
                )
        verified = (
            self._public_key.verify_digest(payload, sig)
            if prehashed
            else self._public_key.verify(payload, sig)
        )
        if not verified:
            raise ForgeBackendError(
                "forge backend returned a signature that does not verify against the wallet public key"
            )


class RemoteWallet(Wallet):
    """A cosmpy Wallet backed by a Forge signing wallet (Privy server wallet).

    The public key and address are fetched from the backend on construction (or supplied
    directly) so the wallet can seal/simulate transactions before it has ever transacted
    on-chain. ``signer()`` returns a :class:`RemoteSigner`.

    Passing ``public_key_hex`` skips the (blocking) wallet-info fetch — useful from async
    contexts. **When you use it, the backend is not contacted at construction, so the
    (api_key, wallet_id) binding and the wallet's existence are NOT verified until the
    first ``sign`` call.** Pass ``address`` alongside ``public_key_hex`` to keep the local
    pubkey↔address cross-check; a wrong ``wallet_id`` will then only surface as a 404/403
    on the first signing request.
    """

    def __init__(
        self,
        backend_url: str,
        api_key: str,
        wallet_id: str,
        prefix: str = "allo",
        timeout: float = DEFAULT_TIMEOUT,
        public_key_hex: Optional[str] = None,
        address: Optional[str] = None,
        client: Optional[ForgeBackendClient] = None,
    ):
        self._wallet_id = wallet_id
        self._prefix = prefix
        self._client = client if client is not None else ForgeBackendClient(backend_url, api_key, timeout)

        # For the public_key_hex shortcut, cross-check against a caller-supplied address.
        reported_address: Optional[str] = address
        if public_key_hex is None:
            info = self._client.get_wallet_info(wallet_id)
            # Guard against a proxy misroute / cache bug returning a different wallet.
            if info.id and info.id != wallet_id:
                raise WalletConfigError(
                    f"forge wallet-info id {info.id} does not match requested wallet_id {wallet_id}"
                )
            public_key_hex = info.pubkey
            reported_address = info.address
            if not public_key_hex:
                raise ForgeBackendError("forge wallet-info response missing 'pubkey'")
            # Fail closed: an empty address would otherwise skip the cross-check below,
            # leaving the (api_key, wallet_id) <-> keypair binding unverified.
            if not reported_address:
                raise ForgeBackendError("forge wallet-info response missing 'address'")

        try:
            pubkey_bytes = bytes.fromhex(public_key_hex)
        except ValueError as e:
            raise WalletConfigError(f"wallet pubkey is not valid hex: {e}") from e
        if len(pubkey_bytes) != 33:
            # Cosmos secp256k1 pubkeys are 33-byte compressed; surface a clear error
            # rather than an obscure ecdsa failure deep inside cosmpy.
            raise WalletConfigError(
                f"expected 33-byte compressed secp256k1 pubkey, got {len(pubkey_bytes)} bytes"
            )
        self._public_key = PublicKey(pubkey_bytes)

        # Cross-check the backend's reported address against the pubkey-derived one so a
        # misconfigured wallet fails here rather than producing rejected transactions.
        derived = str(Address(self._public_key, self._prefix))
        if reported_address and reported_address != derived:
            raise WalletConfigError(
                f"backend address {reported_address} does not match pubkey-derived address {derived}"
            )

        # Pin the pubkey into the signer so it verifies every backend signature locally.
        self._signer = RemoteSigner(self._client, wallet_id, public_key=self._public_key)

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
    public_key_hex: Optional[str] = None,
    address: Optional[str] = None,
    client: Optional[ForgeBackendClient] = None,
) -> RemoteWallet:
    """Construct a backend-backed wallet for the Privy-managed signing path.

    Pass the result to ``AlloraWalletConfig(wallet=...)`` (or ``AlloraWorker`` via its
    wallet config) to sign through the Forge backend instead of a local key. Inject a
    custom ``client`` (e.g. with a tuned :class:`requests.Session`) to control the HTTP
    transport.

    Pass ``public_key_hex`` to skip the blocking wallet-info fetch (useful in async
    contexts such as FastAPI startup or async test fixtures). When you do, also pass
    ``address`` to keep the local pubkey↔address cross-check; note the backend is not
    contacted until the first signing call, so the ``(api_key, wallet_id)`` binding is
    not validated at construction time.
    """
    return RemoteWallet(
        backend_url,
        api_key,
        wallet_id,
        prefix=prefix,
        timeout=timeout,
        public_key_hex=public_key_hex,
        address=address,
        client=client,
    )
