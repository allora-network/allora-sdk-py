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
import urllib.error
import urllib.request
from typing import Optional

from cosmpy.aerial.wallet import Wallet
from cosmpy.crypto.address import Address
from cosmpy.crypto.interface import Signer
from cosmpy.crypto.keypairs import PublicKey

API_KEY_HEADER = "X-Forge-API-Key"
DEFAULT_TIMEOUT = 30.0


class RemoteSigner(Signer):
    """A cosmpy Signer that delegates signing to the Forge backend.

    ``sign`` is used for Cosmos transaction signatures (the backend SHA-256 hashes the
    SignDoc bytes before signing); ``sign_digest`` is used for application-level bundle
    signatures, where the 32-byte digest is signed as-is. Both return a 64-byte
    canonical (low-S) secp256k1 signature.

    The private key never leaves Privy/the backend.
    """

    def __init__(self, backend_url: str, api_key: str, wallet_id: str, timeout: float = DEFAULT_TIMEOUT):
        self._base = backend_url.rstrip("/")
        self._api_key = api_key
        self._wallet_id = wallet_id
        self._timeout = timeout

    def sign(self, message: bytes, deterministic: bool = False, canonicalise: bool = True) -> bytes:
        # The backend hashes the message (SHA-256) before signing, matching cosmpy's
        # local PrivateKey.sign semantics over the SignDoc bytes.
        return self._remote_sign(message, prehashed=False)

    def sign_digest(self, digest: bytes, deterministic: bool = False, canonicalise: bool = True) -> bytes:
        # The digest is already hashed; the backend signs it directly.
        return self._remote_sign(digest, prehashed=True)

    def _remote_sign(self, payload: bytes, prehashed: bool) -> bytes:
        url = f"{self._base}/api/v1/signing-wallets/{self._wallet_id}/sign"
        body = json.dumps({"payload": payload.hex(), "prehashed": prehashed}).encode()
        data = _post_json(url, body, self._api_key, self._timeout)
        signature = data.get("signature")
        if not signature:
            raise RuntimeError("forge sign response missing 'signature'")
        return bytes.fromhex(signature)


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
    ):
        self._base = backend_url.rstrip("/")
        self._api_key = api_key
        self._wallet_id = wallet_id
        self._prefix = prefix
        self._signer = RemoteSigner(backend_url, api_key, wallet_id, timeout)

        reported_address: Optional[str] = None
        if public_key_hex is None:
            info = self._fetch_info(timeout)
            public_key_hex = info.get("pubkey")
            reported_address = info.get("address")
            if not public_key_hex:
                raise RuntimeError("forge wallet-info response missing 'pubkey'")

        self._public_key = PublicKey(bytes.fromhex(public_key_hex))

        # Cross-check the backend's reported address against the pubkey-derived one so a
        # misconfigured wallet fails here rather than producing rejected transactions.
        derived = str(Address(self._public_key, self._prefix))
        if reported_address and reported_address != derived:
            raise RuntimeError(
                f"backend address {reported_address} does not match pubkey-derived address {derived}"
            )

    def _fetch_info(self, timeout: float) -> dict:
        url = f"{self._base}/api/v1/signing-wallets/{self._wallet_id}"
        return _get_json(url, self._api_key, timeout)

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
) -> RemoteWallet:
    """Construct a backend-backed wallet for the Privy-managed signing path.

    Pass the result to ``AlloraWalletConfig(wallet=...)`` (or ``AlloraWorker`` via its
    wallet config) to sign through the Forge backend instead of a local key.
    """
    return RemoteWallet(backend_url, api_key, wallet_id, prefix=prefix, timeout=timeout)


def _get_json(url: str, api_key: str, timeout: float) -> dict:
    req = urllib.request.Request(url, method="GET")
    req.add_header(API_KEY_HEADER, api_key)
    return _do(req, timeout)


def _post_json(url: str, body: bytes, api_key: str, timeout: float) -> dict:
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header(API_KEY_HEADER, api_key)
    return _do(req, timeout)


def _do(req: urllib.request.Request, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"forge backend returned {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"failed to reach forge backend: {e.reason}") from e
