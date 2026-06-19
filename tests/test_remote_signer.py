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
    WalletConfigError,
    make_remote_wallet,
)

WALLET_ID = "11111111-1111-1111-1111-111111111111"


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
            assert self.headers.get("X-Forge-API-Key"), "missing api key header"
            self._send({"id": WALLET_ID, "address": address, "pubkey": pub_hex})

        def do_POST(self):
            assert self.headers.get("X-Forge-API-Key"), "missing api key header"
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length))
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


def test_remote_wallet_address_and_pubkey(backend):
    priv, url = backend
    wallet = make_remote_wallet(url, "forge_sk_test", WALLET_ID)
    assert str(wallet.address()) == str(Address(priv.public_key, "allo"))
    assert wallet.public_key().public_key_bytes == priv.public_key.public_key_bytes


def test_remote_signer_sign_verifies(backend):
    priv, url = backend
    wallet = make_remote_wallet(url, "forge_sk_test", WALLET_ID)
    message = b"cosmos signdoc bytes"
    sig = wallet.signer().sign(message)
    assert priv.public_key.verify(message, sig)


def test_remote_signer_sign_digest_verifies(backend):
    priv, url = backend
    wallet = make_remote_wallet(url, "forge_sk_test", WALLET_ID)
    digest = hashlib.sha256(b"worker bundle bytes").digest()
    sig = wallet.signer().sign_digest(digest)
    assert priv.public_key.verify_digest(digest, sig)


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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(WalletConfigError, match="does not match"):
            make_remote_wallet(url, "forge_sk_test", WALLET_ID)
    finally:
        server.shutdown()


def test_non_https_backend_url_rejected():
    # Plain http:// to a non-loopback host would leak the API key in cleartext.
    with pytest.raises(ValueError, match="https"):
        make_remote_wallet("http://forge.example.com", "forge_sk_test", WALLET_ID)


def test_redirect_is_not_followed():
    # A redirecting backend must not have the X-Forge-API-Key re-sent on the next hop;
    # the client disables redirects and treats the 3xx as a backend error.
    class RedirectHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/leak")
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(ForgeBackendError):
            make_remote_wallet(url, "forge_sk_test", WALLET_ID)
    finally:
        server.shutdown()
        thread.join(timeout=2)
