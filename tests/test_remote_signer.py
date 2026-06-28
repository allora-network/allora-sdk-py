"""Tests for the Privy-managed (delegated) remote signer.

A local HTTP server stands in for the Forge backend and signs with a real cosmpy
private key, so the test exercises the full request/response path and proves the
signatures the RemoteSigner returns verify against the wallet's public key.
"""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from cosmpy.crypto.address import Address
from cosmpy.crypto.keypairs import PrivateKey

from allora_sdk.rpc_client.remote_signer import (
    ForgeBackendError,
    SigningWalletInfo,
    WalletConfigError,
    make_remote_wallet,
)

WALLET_ID = "11111111-1111-1111-1111-111111111111"
API_KEY = "forge_sk_test"


def _make_handler(priv: PrivateKey):
    pub_hex = priv.public_key.public_key_bytes.hex()
    address = str(Address(priv.public_key, "allo"))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence test server logging
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            assert self.headers.get("X-Forge-API-Key") == API_KEY, "wrong/missing api key header"
            self._send({"id": WALLET_ID, "address": address, "pubkey": pub_hex})

        def do_POST(self):
            assert self.headers.get("X-Forge-API-Key") == API_KEY, "wrong/missing api key header"
            if self.path.endswith("/clear-association"):
                # Clear-association carries no request body (and thus no Content-Type).
                self._send({"message": "association cleared"})
                return
            assert self.headers.get("Content-Type") == "application/json", "missing/wrong content-type"
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length))
            if not self.path.endswith("/sign"):
                # Provision (POST /api/v1/signing-wallets with topic_id): get-or-create wallet.
                self._send({"id": WALLET_ID, "address": address, "pubkey": pub_hex, "topic_id": req.get("topic_id")})
                return
            payload = bytes.fromhex(req["payload"])
            sig = priv.sign_digest(payload) if req["prehashed"] else priv.sign(payload)
            self._send({"signature": sig.hex(), "pubkey": pub_hex})

    return Handler


@pytest.fixture
def backend():
    priv = PrivateKey()
    server = HTTPServer(("127.0.0.1", 0), _make_handler(priv))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield priv, url
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_remote_wallet_address_and_pubkey(backend):
    priv, url = backend
    wallet = make_remote_wallet(url, API_KEY, WALLET_ID)
    assert str(wallet.address()) == str(Address(priv.public_key, "allo"))
    assert wallet.public_key().public_key_bytes == priv.public_key.public_key_bytes


def test_remote_signer_sign_verifies(backend):
    priv, url = backend
    wallet = make_remote_wallet(url, API_KEY, WALLET_ID)
    message = b"cosmos signdoc bytes"
    sig = wallet.signer().sign(message)
    assert priv.public_key.verify(message, sig)


def test_remote_signer_sign_digest_verifies(backend):
    priv, url = backend
    wallet = make_remote_wallet(url, API_KEY, WALLET_ID)
    digest = hashlib.sha256(b"worker bundle bytes").digest()
    sig = wallet.signer().sign_digest(digest)
    assert priv.public_key.verify_digest(digest, sig)


def test_from_env_builds_remote_wallet(backend, monkeypatch):
    from allora_sdk.rpc_client.config import AlloraWalletConfig
    from allora_sdk.rpc_client.remote_signer import RemoteWallet

    priv, url = backend
    monkeypatch.setenv("FORGE_API_KEY", API_KEY)
    monkeypatch.setenv("FORGE_SIGNING_WALLET_ID", WALLET_ID)
    monkeypatch.setenv("FORGE_BACKEND_URL", url)

    cfg = AlloraWalletConfig.from_env()
    assert isinstance(cfg.wallet, RemoteWallet)
    assert str(cfg.wallet.address()) == str(Address(priv.public_key, "allo"))


def test_from_env_reads_fee_granter(backend, monkeypatch):
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    _priv, url = backend
    monkeypatch.setenv("FORGE_API_KEY", API_KEY)
    monkeypatch.setenv("FORGE_SIGNING_WALLET_ID", WALLET_ID)
    monkeypatch.setenv("FORGE_BACKEND_URL", url)
    monkeypatch.setenv("FEE_GRANTER", "allo1granteraddrxxxxxxxxxxxxxxxxxxxxxxxxx")

    cfg = AlloraWalletConfig.from_env()
    assert cfg.fee_granter == "allo1granteraddrxxxxxxxxxxxxxxxxxxxxxxxxx"


def test_clear_association(backend):
    from allora_sdk.rpc_client.remote_signer import ForgeBackendClient

    _priv, url = backend
    client = ForgeBackendClient(url, API_KEY)
    # A 2xx response returns without raising (the topic binding was released).
    client.clear_association(WALLET_ID)


def test_provision_remote_wallet(backend):
    from allora_sdk.rpc_client.remote_signer import provision_remote_wallet

    priv, url = backend
    wallet = provision_remote_wallet(url, API_KEY, topic_id=42)
    assert str(wallet.address()) == str(Address(priv.public_key, "allo"))
    # The provisioned wallet signs through the same backend.
    sig = wallet.signer().sign(b"signdoc bytes")
    assert priv.public_key.verify(b"signdoc bytes", sig)


def test_from_env_defers_managed_provision(monkeypatch):
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    # API key but no wallet id: from_env defers provisioning (no wallet built, no network call).
    monkeypatch.setenv("FORGE_API_KEY", API_KEY)
    monkeypatch.delenv("FORGE_SIGNING_WALLET_ID", raising=False)
    monkeypatch.setenv("FORGE_BACKEND_URL", "https://forge.invalid")

    cfg = AlloraWalletConfig.from_env()
    assert cfg.wallet is None
    assert cfg.forge_api_key == API_KEY


def test_init_worker_wallet_provisions_for_topic(backend):
    from allora_sdk.rpc_client.config import AlloraWalletConfig
    from allora_sdk.worker.utils import init_worker_wallet

    priv, url = backend
    cfg = AlloraWalletConfig(forge_api_key=API_KEY, forge_backend_url=url)
    wallet = init_worker_wallet(cfg, topic_id=7)
    assert str(wallet.address()) == str(Address(priv.public_key, "allo"))


def test_init_worker_wallet_managed_requires_topic():
    from allora_sdk.rpc_client.config import AlloraWalletConfig
    from allora_sdk.worker.utils import init_worker_wallet

    cfg = AlloraWalletConfig(forge_api_key=API_KEY, forge_backend_url="https://forge.invalid")
    with pytest.raises(ValueError, match="topic_id"):
        init_worker_wallet(cfg, topic_id=None)


def test_from_env_conflicting_credentials_rejected(monkeypatch):
    # Forge env vars set alongside a stale local key (a common mid-migration state) must
    # fail loudly, not silently sign through Forge — mirrors __post_init__'s single-source
    # guard. The check fires before any backend contact.
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    monkeypatch.setenv("FORGE_API_KEY", API_KEY)
    monkeypatch.setenv("FORGE_SIGNING_WALLET_ID", WALLET_ID)
    monkeypatch.setenv("PRIVATE_KEY", "deadbeef")
    with pytest.raises(ValueError, match="exactly one signing source"):
        AlloraWalletConfig.from_env()


def test_public_key_hex_shortcut_skips_fetch():
    # With public_key_hex (+ address) the constructor must not contact the backend,
    # so async callers can build a wallet without a blocking GET.
    priv = PrivateKey()
    pub_hex = priv.public_key.public_key_bytes.hex()
    address = str(Address(priv.public_key, "allo"))
    wallet = make_remote_wallet(
        "https://forge.invalid",
        "forge_sk_test",
        WALLET_ID,
        public_key_hex=pub_hex,
        address=address,
    )
    assert str(wallet.address()) == address
    assert wallet.public_key().public_key_bytes == priv.public_key.public_key_bytes


def test_public_key_hex_shortcut_address_mismatch_raises():
    priv = PrivateKey()
    pub_hex = priv.public_key.public_key_bytes.hex()
    with pytest.raises(WalletConfigError, match="does not match"):
        make_remote_wallet(
            "https://forge.invalid",
            "forge_sk_test",
            WALLET_ID,
            public_key_hex=pub_hex,
            address="allo1wrongaddressxxxxxxxxxxxxxxxxxxxxxxxxxx",
        )


def test_non_uuid_wallet_id_rejected():
    # forge-v2 keys signing wallets by Privy UUID; a non-UUID wallet_id is a config typo and
    # must fail locally (parity with allora-sdk-go's uuid.Parse guard) without a network call.
    with pytest.raises(WalletConfigError, match="UUID"):
        make_remote_wallet("https://forge.invalid", API_KEY, "not-a-uuid")


def test_signature_not_matching_pubkey_rejected():
    # The wallet pins the pubkey from wallet-info; a signature that does not verify
    # against it (backend bug / MITM) must be rejected locally, not broadcast.
    good = PrivateKey()
    bad = PrivateKey()
    good_pub = good.public_key.public_key_bytes.hex()
    address = str(Address(good.public_key, "allo"))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"id": WALLET_ID, "address": address, "pubkey": good_pub})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length))
            payload = bytes.fromhex(req["payload"])
            sig = bad.sign_digest(payload) if req["prehashed"] else bad.sign(payload)
            self._send({"signature": sig.hex(), "pubkey": good_pub})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        wallet = make_remote_wallet(url, API_KEY, WALLET_ID)
        with pytest.raises(ForgeBackendError, match="does not verify"):
            wallet.signer().sign(b"cosmos signdoc bytes")
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_address_mismatch_raises():
    # A backend that reports an address inconsistent with the pubkey must be rejected.
    priv = PrivateKey()
    pub_hex = priv.public_key.public_key_bytes.hex()

    class BadHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps(
                {"id": WALLET_ID, "address": "allo1wrongaddressxxxxxxxxxxxxxxxxxxxxxxxxxx", "pubkey": pub_hex}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), BadHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(WalletConfigError, match="does not match"):
            make_remote_wallet(url, API_KEY, WALLET_ID)
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_empty_wallet_id_fails_closed():
    # A wallet-info response with an empty id must fail closed: it cannot bind the
    # response to the requested wallet, so a mis-routed / cache-poisoned response that
    # is otherwise internally consistent is not silently accepted (parity with
    # allora-sdk-go and allora-sdk-ts).
    priv = PrivateKey()
    pub_hex = priv.public_key.public_key_bytes.hex()
    address = str(Address(priv.public_key, "allo"))

    class EmptyIdHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"id": "", "address": address, "pubkey": pub_hex}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), EmptyIdHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(WalletConfigError, match="missing 'id'"):
            make_remote_wallet(url, API_KEY, WALLET_ID)
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_non_json_response_raises():
    # A 200 with a non-JSON body (e.g. a gateway HTML page) must surface as a clear
    # ForgeBackendError, not a bare JSONDecodeError.
    class HtmlHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b"<html>gateway error</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), HtmlHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(ForgeBackendError, match="non-JSON"):
            make_remote_wallet(url, API_KEY, WALLET_ID)
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_signing_wallet_info_models_full_contract():
    # forge-v2's wallet-info DTO returns evm_address / privy_wallet_id / label / created_at
    # alongside id/address/pubkey. They are modeled as optional metadata (not silently
    # dropped); any further unknown field is still tolerated (lenient client).
    info = SigningWalletInfo.model_validate(
        {
            "id": WALLET_ID,
            "address": "allo1xyz",
            "pubkey": "ab" * 33,
            "evm_address": "0xabc",
            "privy_wallet_id": "privy-123",
            "label": "my-wallet",
            "created_at": "2024-01-02T03:04:05Z",
            "some_future_field": "ignored",
        }
    )
    assert info.evm_address == "0xabc"
    assert info.privy_wallet_id == "privy-123"
    assert info.label == "my-wallet"
    assert info.created_at == "2024-01-02T03:04:05Z"


def test_non_https_backend_url_rejected():
    # Plain http:// to a non-loopback host would leak the API key in cleartext.
    with pytest.raises(ValueError, match="https"):
        make_remote_wallet("http://forge.example.com", API_KEY, WALLET_ID)


def test_redirect_is_not_followed():
    # A redirecting backend must not have the X-Forge-API-Key re-sent on the next hop. Point
    # the redirect at a second "leak" server and assert it is never contacted, so a future
    # change that re-enables redirects (which would forward the key) fails this test — not
    # just that the 3xx surfaces as an error.
    leak_requests: list[str] = []

    class LeakHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            # Record the API-key header (if any) so a leak is observable, then 200.
            leak_requests.append(self.headers.get("X-Forge-API-Key", "<none>"))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    leak_server = HTTPServer(("127.0.0.1", 0), LeakHandler)
    leak_thread = threading.Thread(target=leak_server.serve_forever, daemon=True)
    leak_thread.start()
    leak_url = f"http://127.0.0.1:{leak_server.server_address[1]}/leak"

    class RedirectHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", leak_url)
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(ForgeBackendError):
            make_remote_wallet(url, API_KEY, WALLET_ID)
        # The redirect target must never be contacted, so the X-Forge-API-Key was not re-sent.
        assert leak_requests == [], (
            f"client followed the redirect and leaked headers to the target: {leak_requests}"
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        leak_server.shutdown()
        leak_thread.join(timeout=2)


def test_sign_response_uppercase_pubkey_accepted():
    # bytes.hex() is lowercase, but a backend / proxy could return uppercase hex; the
    # pubkey match must compare decoded bytes, not case-sensitive strings, so a valid
    # signature is not falsely rejected (parity with allora-sdk-go / allora-sdk-ts).
    priv = PrivateKey()
    pub_hex = priv.public_key.public_key_bytes.hex()
    address = str(Address(priv.public_key, "allo"))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"id": WALLET_ID, "address": address, "pubkey": pub_hex})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length))
            payload = bytes.fromhex(req["payload"])
            sig = priv.sign_digest(payload) if req["prehashed"] else priv.sign(payload)
            # Return the pubkey in UPPERCASE hex to exercise the case-insensitive compare.
            self._send({"signature": sig.hex(), "pubkey": pub_hex.upper()})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        wallet = make_remote_wallet(url, API_KEY, WALLET_ID)
        message = b"cosmos signdoc bytes"
        sig = wallet.signer().sign(message)
        assert priv.public_key.verify(message, sig)
    finally:
        server.shutdown()
        thread.join(timeout=2)
