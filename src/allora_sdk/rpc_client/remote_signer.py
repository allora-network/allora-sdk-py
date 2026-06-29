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
import uuid
from typing import Any, Optional, TypeVar

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import HTTPError as Urllib3HTTPError
from urllib3.util.retry import Retry
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
    allora-sdk-ts, and forge-v2. ``id``/``address``/``pubkey`` are required; the
    remaining fields are optional metadata forge-v2 returns. They are modeled
    explicitly (rather than silently dropped) so the contract is documented here.
    Unknown future fields are still ignored — a lenient client, matching
    allora-sdk-go's response struct.

    forge-v2's ``service.SigningWalletInfo`` tags ``PrivyWalletID`` with ``json:"-"``, so the
    Privy server-wallet id is never serialized on the wire — it is intentionally not modeled
    here (it would always be ``None``). ``topic_id``/``worker_label`` are the bound-topic
    metadata the server actually returns (omitempty) for a managed wallet.

    ``master_granter`` is the master/subsidy wallet (the on-chain feegrant fee payer) the
    backend has configured for the wallet. The worker uses it as the ``fee_granter`` fallback
    when none is set explicitly or via ``FORGE_MASTER_GRANTER_ADDRESS`` (both of which
    override it); see ``worker.utils.resolve_fee_granter``.
    """

    id: str
    address: str
    pubkey: str  # hex-encoded 33-byte compressed secp256k1 public key
    evm_address: Optional[str] = None  # 0x... Privy EVM address (cross-check)
    label: Optional[str] = None
    topic_id: Optional[int] = None  # bound topic id (None when unassigned)
    worker_label: Optional[str] = None  # display-only worker hint
    created_at: Optional[str] = None  # RFC 3339 timestamp, raw string (unused)
    master_granter: Optional[str] = None  # allo1... feegrant fee payer (fallback)


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
    in cleartext. Plain http:// is permitted only for loopback (local development).

    Also reject malformed URLs that would otherwise weaken the boundary: embedded
    userinfo (``user:password@host`` is sent as a Basic auth header alongside the API
    key on every request), a missing hostname (otherwise an opaque ConnectionError at
    first use), or a query string / fragment (URL-encoded into every request path).
    Mirrors the tightened allora-sdk-go boundary.
    """
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname:
        raise ValueError(f"backend_url must have a hostname, got: {url!r}")
    if parsed.username or parsed.password:
        raise ValueError(
            "backend_url must not contain userinfo (user:password@host); those "
            "credentials would be sent alongside the API key on every request"
        )
    if parsed.query or parsed.fragment:
        raise ValueError(
            f"backend_url must not contain a query string or fragment, got: {url!r}"
        )
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
        if session is not None:
            # Injected session: the caller owns its lifecycle, so close() must leave it open.
            self._session = session
            self._owns_session = False
            return
        self._owns_session = True
        self._session = requests.Session()
        # Retry only idempotent GET requests (e.g. wallet-info) on a transient 5xx/connection
        # blip. POST is deliberately excluded: clear_association is not idempotent from the
        # client's perspective — a retry after the backend already applied the clear surfaces a
        # spurious 404 on the second attempt. urllib3's retry policy is per-method, not per-route,
        # so clear_association cannot be singled out; a failed sign/provision instead surfaces and
        # is re-driven by the caller on the next nonce. redirect=0 preserves the no-redirect
        # security property (the X-Forge-API-Key is never re-sent on a 3xx); raise_on_status=False
        # lets the final non-2xx response flow into _request's normal error handling instead of
        # raising a urllib3 MaxRetryError.
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            redirect=0,
            backoff_factor=0.5,
            status_forcelist=(502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def close(self) -> None:
        """Close the underlying HTTP session (releasing its connection pool) if this client
        created it. A caller-injected session is left open — the caller owns its lifecycle.
        Idempotent: safe to call more than once.
        """
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> "ForgeBackendClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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

    def provision_wallet(
        self, topic_id: int, label: Optional[str] = None
    ) -> SigningWalletInfo:
        """Idempotently get-or-create the user's signing wallet bound to ``topic_id`` and return
        its non-secret info (id, address, pubkey). Rides on POST /api/v1/signing-wallets with a
        ``topic_id`` body (a static /provision sub-route collides with /:id in the backend router).
        Safe to call on every worker start: the backend enforces one wallet per (user, topic).
        """
        body: dict[str, Any] = {"topic_id": topic_id}
        if label:
            body["label"] = label
        raw = self._request("POST", "/api/v1/signing-wallets", json.dumps(body))
        return _validate(SigningWalletInfo, raw, "provision-wallet")

    def clear_association(self, wallet_id: str) -> None:
        """Release a managed wallet's topic binding (Forge-side bookkeeping only; does NOT
        unregister the worker on-chain). Mirrors POST
        /api/v1/signing-wallets/{id}/clear-association. Raises :class:`ForgeBackendError` on a
        non-2xx response (e.g. 404 for an unknown / foreign / already-cleared wallet), so the
        caller decides whether an unbind failure is fatal or best-effort.
        """
        wid = urllib.parse.quote(wallet_id, safe="")
        self._request("POST", f"/api/v1/signing-wallets/{wid}/clear-association")

    def revoke_wallet(self, wallet_id: str) -> None:
        """Permanently revoke (delete) a managed signing wallet via DELETE
        /api/v1/signing-wallets/{id}. Unlike :meth:`clear_association` (which only releases the
        topic binding), this deletes the wallet itself; it does NOT unregister the worker
        on-chain. ``wallet_id`` is validated as a UUID locally (forge-v2 keys signing wallets by
        Privy UUID) so a typo fails fast as a :class:`WalletConfigError` rather than as an opaque
        404 after a destructive call is issued. Like clear_association the DELETE is not auto-
        retried (the session retries only idempotent GETs), and a non-2xx response raises
        :class:`ForgeBackendError` (e.g. 404 for an unknown / foreign / already-revoked wallet),
        so the caller decides whether a revoke failure is fatal or best-effort.
        """
        try:
            wallet_id = str(uuid.UUID(wallet_id))
        except ValueError as e:
            raise WalletConfigError(
                f"wallet_id must be a UUID, got {wallet_id!r}: {e}"
            ) from e
        wid = urllib.parse.quote(wallet_id, safe="")
        self._request("DELETE", f"/api/v1/signing-wallets/{wid}")

    def _request(
        self, method: str, path: str, body: Optional[str] = None
    ) -> dict[str, Any]:
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

        # The body read is a second failure point: a connection dropped mid-body, a corrupt
        # gzip/deflate stream (decode_content=True), or a read timeout raises a urllib3
        # HTTPError (ProtocolError/DecodeError/ReadTimeoutError) or a raw socket OSError —
        # none of which subclass requests.RequestException, so they would otherwise escape the
        # ForgeBackendError contract that callers (_remote_sign, wallet-info, provisioning) rely on.
        try:
            with resp:
                raw = resp.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
        except (requests.RequestException, Urllib3HTTPError, OSError) as e:
            raise ForgeBackendError(
                f"failed to read forge backend response: {e}"
            ) from e
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ForgeBackendError(
                f"forge backend response exceeded {MAX_RESPONSE_BYTES} bytes"
            )

        if not (200 <= resp.status_code < 300):
            # Truncate: backend 4xx bodies can reflect request fields / wallet ids, and
            # this message bubbles up to operator logs.
            detail = raw.decode(errors="replace")[:512]
            raise ForgeBackendError(
                f"forge backend returned {resp.status_code}: {detail}"
            )

        if not raw:
            # A 2xx with an empty body (e.g. 204 No Content from clear-association) is a
            # success with no JSON payload. Return an empty mapping rather than failing the
            # JSON parse; callers that need fields validate them separately and would still
            # surface a clear "unexpected response" error if the body were wrongly empty.
            return {}

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

    def sign(
        self, message: bytes, deterministic: bool = False, canonicalise: bool = True
    ) -> bytes:
        """Sign Cosmos SignDoc bytes via the Forge backend (the backend SHA-256 hashes the
        message before signing, matching cosmpy's local ``PrivateKey.sign``).

        ``deterministic`` is accepted for cosmpy ``Signer`` compatibility but has NO effect:
        the Forge backend always signs deterministically (RFC 6979), so ``deterministic=False``
        (a request for randomized k) still returns a deterministic signature. This is
        asymmetric with ``canonicalise=False`` — which is rejected because the backend cannot
        honor it — on purpose: a deterministic signature is valid for any requested value, and
        cosmpy's transaction path passes ``deterministic=False`` by default, so raising here
        would break ordinary signing.
        """
        self._check_canonicalise(canonicalise)
        return self._remote_sign(message, prehashed=False)

    def sign_digest(
        self, digest: bytes, deterministic: bool = False, canonicalise: bool = True
    ) -> bytes:
        """Sign a pre-hashed 32-byte digest via the Forge backend (used for bundle
        signatures; the backend signs the digest as-is).

        ``deterministic`` is accepted for cosmpy ``Signer`` compatibility but has NO effect —
        see :meth:`sign` for the rationale (the backend always uses RFC 6979).
        """
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
        if prehashed and len(payload) != 32:
            # A prehashed payload is a SHA-256 digest; verify_digest expects exactly 32 bytes,
            # so a wrong-length digest would otherwise fail with an obscure cosmpy error after a
            # needless backend round-trip. Reject at the boundary (symmetric with the 64-byte
            # signature-length check below).
            raise ValueError(
                f"prehashed digest must be exactly 32 bytes, got {len(payload)}"
            )
        result = self._client.sign(self._wallet_id, payload, prehashed)
        if not result.signature:
            raise ForgeBackendError("forge sign response missing 'signature'")
        try:
            sig = bytes.fromhex(result.signature)
        except ValueError as e:
            raise ForgeBackendError(
                f"forge sign response 'signature' is not valid hex: {e}"
            ) from e
        if len(sig) != 64:
            # Cosmos secp256k1 signatures are exactly 64 raw bytes (r || s). A 65-byte
            # recoverable form or DER encoding would be rejected on-chain with an opaque
            # error, so fail fast here instead.
            raise ForgeBackendError(
                f"forge sign response signature has wrong length {len(sig)} (expected 64)"
            )
        self._verify(payload, sig, prehashed, result.pubkey)
        return sig

    def _verify(
        self,
        payload: bytes,
        sig: bytes,
        prehashed: bool,
        response_pubkey: Optional[str],
    ) -> None:
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

    On construction it also captures the backend-reported ``master_granter`` (if any) as the
    ``fee_granter`` attribute, so a worker on the managed path can subsidize gas from that
    feegrant without explicit config. An explicit ``fee_granter`` (or
    ``FORGE_MASTER_GRANTER_ADDRESS``) overrides the discovered value at the worker/config layer.

    Passing ``public_key_hex`` skips the (blocking) wallet-info fetch — useful from async
    contexts. **When you use it, the backend is not contacted at construction, so the
    (api_key, wallet_id) binding and the wallet's existence are NOT verified until the
    first ``sign`` call.** Pass ``address`` alongside ``public_key_hex`` to keep the local
    pubkey↔address cross-check; a wrong ``wallet_id`` will then only surface as a 404/403
    on the first signing request.

    Latency note: every worker nonce makes two sequential, blocking HTTPS round-trips to
    the Forge backend — the bundle signature (``sign_digest``) and the transaction
    signature (``sign``) — so per-nonce wall time is roughly ``2 × backend_RTT`` (e.g.
    ~400ms at a 200ms RTT, versus microseconds for local signing). The two calls are
    inherently sequential: the bundle signature is embedded in the request before the tx
    is built. Operators on the RemoteWallet path should size ``max_unfulfilled_nonces``
    and their round windows accordingly — the defaults were calibrated for fast local
    signing. The signing HTTP timeout is the ``timeout`` argument (default
    ``DEFAULT_TIMEOUT`` = 30s), shared with the wallet-info fetch.
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
        fee_granter: Optional[str] = None,
    ):
        # forge-v2 keys signing wallets by Privy UUID, so a non-UUID wallet_id is always a
        # config bug (e.g. a typo in FORGE_SIGNING_WALLET_ID). Fail locally with a clear error
        # rather than as an opaque 404 on the first request (parity with allora-sdk-go's
        # uuid.Parse guard). Normalize to the canonical dashed form so non-canonical input
        # (bare hex, urn:uuid:..., {braced}) is not URL-encoded into the backend path verbatim,
        # and so the id cross-check below compares against the backend's canonical id.
        try:
            wallet_id = str(uuid.UUID(wallet_id))
        except ValueError as e:
            raise WalletConfigError(
                f"wallet_id must be a UUID, got {wallet_id!r}: {e}"
            ) from e

        self._wallet_id = wallet_id
        self._prefix = prefix
        self._client = (
            client
            if client is not None
            else ForgeBackendClient(backend_url, api_key, timeout)
        )

        # Build the keypair/signer and resolve the fee-granter from the backend (or the supplied
        # values). If any step fails — wallet-info fetch, or hex/length/address validation — close
        # the HTTP session we opened so a mid-construction error doesn't leak the connection pool.
        # A caller-injected client is left open for the caller to manage.
        try:
            self._init_from_backend(wallet_id, public_key_hex, address, fee_granter)
        except Exception:
            if client is None:
                self._client.close()
            raise

    def _init_from_backend(
        self,
        wallet_id: str,
        public_key_hex: Optional[str],
        address: Optional[str],
        fee_granter: Optional[str],
    ) -> None:
        """Resolve the keypair, signer, and fee-granter from the backend (or supplied values).

        Split out of ``__init__`` so an owned ``ForgeBackendClient`` can be closed if any step
        here raises, rather than leaking its HTTP session.
        """
        # For the public_key_hex shortcut, cross-check against a caller-supplied address.
        reported_address: Optional[str] = address
        # The managed master granter (feegrant fee payer) the backend reports for this wallet.
        # provision_remote_wallet, which already has it from the provision response, passes it
        # in to skip rediscovery; otherwise it is read from wallet-info below.
        discovered_granter: Optional[str] = fee_granter
        if public_key_hex is None:
            info = self._client.get_wallet_info(wallet_id)
            # Guard against a proxy misroute / cache bug returning a different wallet.
            # Fail closed on an empty id: a backend that omits it cannot be trusted to have
            # bound the response to the requested wallet, and the pubkey<->address cross-check
            # below would not catch a mis-routed-but-internally-consistent response (parity with
            # allora-sdk-go's `info.ID == ""` guard and allora-sdk-ts).
            if not info.id:
                raise WalletConfigError(
                    f"forge wallet-info response for {wallet_id} missing 'id'; cannot verify "
                    "the backend bound the response to the requested wallet"
                )
            if info.id != wallet_id:
                raise WalletConfigError(
                    f"forge wallet-info id {info.id} does not match requested wallet_id {wallet_id}"
                )
            public_key_hex = info.pubkey
            if not public_key_hex:
                raise ForgeBackendError("forge wallet-info response missing 'pubkey'")
            # Fail closed: an empty address would otherwise skip the cross-check below,
            # leaving the (api_key, wallet_id) <-> keypair binding unverified.
            if not info.address:
                raise ForgeBackendError("forge wallet-info response missing 'address'")
            # Honor a caller-supplied address as a trust-but-verify assertion rather than
            # silently overwriting it with the backend's value (defense in depth: a caller
            # passing address='allo1...' is asserting "this wallet_id maps to this address").
            if reported_address is not None and reported_address != info.address:
                raise WalletConfigError(
                    f"backend address {info.address} does not match "
                    f"caller-supplied address {reported_address}"
                )
            reported_address = info.address
            if discovered_granter is None:
                discovered_granter = info.master_granter

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
        self._signer = RemoteSigner(
            self._client, wallet_id, public_key=self._public_key
        )

        # Defense-in-depth: validate a backend-discovered granter at this boundary (bech32 +
        # 20-byte payload + HRP == wallet prefix), matching AlloraWalletConfig._validate_fee_granter
        # so EVERY consumer of wallet.fee_granter — not just the worker/config paths — gets a
        # checked value. A misbehaving backend then fails fast here instead of surfacing as an
        # opaque on-chain "feegrant not found" at broadcast time.
        if discovered_granter:
            try:
                parsed = Address(discovered_granter)
            except Exception as e:
                raise WalletConfigError(
                    f"backend master_granter {discovered_granter!r} is not a "
                    f"valid address: {e}"
                ) from e
            if len(bytes(parsed)) != 20:
                raise WalletConfigError(
                    f"backend master_granter {discovered_granter!r} is not a "
                    "20-byte account address"
                )
            granter_hrp = discovered_granter.rsplit("1", 1)[0]
            if granter_hrp != self._prefix:
                raise WalletConfigError(
                    f"backend master_granter HRP {granter_hrp!r} does not match "
                    f"wallet prefix {self._prefix!r}"
                )

        # Discovered feegrant fee payer, exposed for the worker's fee_granter fallback (see
        # worker.utils.resolve_fee_granter). Normalize an empty backend value to None ("unset").
        self.fee_granter: Optional[str] = discovered_granter or None

    def address(self) -> Address:
        return Address(self._public_key, self._prefix)

    def public_key(self) -> PublicKey:
        return self._public_key

    def signer(self) -> Signer:
        return self._signer

    def close(self) -> None:
        """Release the Forge backend HTTP connection pool. Idempotent.

        A no-op when this wallet was built around a caller-injected ForgeBackendClient whose
        session the caller owns (ForgeBackendClient.close honors session ownership).
        """
        self._client.close()

    def __enter__(self) -> "RemoteWallet":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


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


def provision_remote_wallet(
    backend_url: str,
    api_key: str,
    topic_id: int,
    label: Optional[str] = None,
    prefix: str = "allo",
    timeout: float = DEFAULT_TIMEOUT,
    client: Optional[ForgeBackendClient] = None,
) -> RemoteWallet:
    """Idempotently get-or-create the user's managed wallet bound to ``topic_id`` (one worker =
    one topic) and return a :class:`RemoteWallet` for it. Safe to call on every worker start.

    The provisioned wallet's pubkey/address are returned by the provision call, so the resulting
    RemoteWallet is built without a second (blocking) wallet-info fetch. If the provision response
    carries a ``master_granter`` (the backend-configured feegrant fee payer), it is captured on
    the wallet as ``fee_granter`` so a worker can subsidize gas without explicit config; an
    explicit fee_granter / ``FORGE_MASTER_GRANTER_ADDRESS`` overrides it at the worker layer.
    """
    c = (
        client
        if client is not None
        else ForgeBackendClient(backend_url, api_key, timeout)
    )
    try:
        info = c.provision_wallet(topic_id, label)
        return RemoteWallet(
            backend_url,
            api_key,
            info.id,
            prefix=prefix,
            timeout=timeout,
            public_key_hex=info.pubkey,
            address=info.address,
            client=c,
            fee_granter=info.master_granter,
        )
    except Exception:
        # Close the client we created if provisioning or wallet construction fails, so the owned
        # HTTP session isn't leaked. A caller-injected client is left for the caller to manage.
        if client is None:
            c.close()
        raise
