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
# A valid allo1 master-granter address the fake backend reports on wallet-info / provision.
MASTER_GRANTER = str(Address(PrivateKey().public_key, "allo"))


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
            self._send(
                {
                    "id": WALLET_ID,
                    "address": address,
                    "pubkey": pub_hex,
                    "master_granter": MASTER_GRANTER,
                }
            )

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
                self._send(
                    {
                        "id": WALLET_ID,
                        "address": address,
                        "pubkey": pub_hex,
                        "topic_id": req.get("topic_id"),
                        "master_granter": MASTER_GRANTER,
                    }
                )
                return
            payload = bytes.fromhex(req["payload"])
            sig = priv.sign_digest(payload) if req["prehashed"] else priv.sign(payload)
            self._send({"signature": sig.hex(), "pubkey": pub_hex})

        def do_DELETE(self):
            assert self.headers.get("X-Forge-API-Key") == API_KEY, "wrong/missing api key header"
            # DELETE /api/v1/signing-wallets/:id — revoke (delete) the signing wallet.
            self._send({"message": "wallet revoked"})

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


def test_sign_ignores_deterministic_flag_and_rejects_non_canonical(backend):
    # deterministic is accepted for cosmpy Signer compatibility but has no effect (the
    # backend always uses RFC 6979). It must not raise — cosmpy's tx path passes
    # deterministic=False by default — unlike canonicalise=False, which is rejected.
    priv, url = backend
    wallet = make_remote_wallet(url, API_KEY, WALLET_ID)
    message = b"cosmos signdoc bytes"
    sig = wallet.signer().sign(message, deterministic=False)
    assert priv.public_key.verify(message, sig)
    with pytest.raises(ValueError, match="canonicalise"):
        wallet.signer().sign(message, canonicalise=False)


def test_caller_supplied_address_mismatch_rejected(backend):
    # Passing address= without public_key_hex is a "this wallet_id maps to this address"
    # assertion. If it disagrees with the backend it must fail, not be silently overwritten.
    _priv, url = backend
    wrong = str(Address(PrivateKey().public_key, "allo"))
    with pytest.raises(WalletConfigError, match="does not match caller-supplied address"):
        make_remote_wallet(url, API_KEY, WALLET_ID, address=wrong)


def test_caller_supplied_address_match_accepted(backend):
    # A correct caller-supplied address passes the cross-check and builds the wallet.
    priv, url = backend
    correct = str(Address(priv.public_key, "allo"))
    wallet = make_remote_wallet(url, API_KEY, WALLET_ID, address=correct)
    assert str(wallet.address()) == correct


def test_forge_backend_retries_transient_5xx():
    # A transient 503 must be retried (the call is idempotent), not fail the wallet build outright.
    priv = PrivateKey()
    pub_hex = priv.public_key.public_key_bytes.hex()
    address = str(Address(priv.public_key, "allo"))
    state = {"calls": 0}

    class FlakyHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["calls"] += 1
            if state["calls"] == 1:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps({"id": WALLET_ID, "address": address, "pubkey": pub_hex}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), FlakyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        wallet = make_remote_wallet(url, API_KEY, WALLET_ID)
        assert str(wallet.address()) == address
        assert state["calls"] == 2  # first 503 retried, second 200 succeeded
    finally:
        server.shutdown()
        thread.join(timeout=2)


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
    # The SDK built this RemoteWallet, so it is client-owned and its backend session is
    # released by AlloraRPCClient.close() (not leaked until process exit).
    assert cfg._sdk_owned is True


def test_caller_supplied_wallet_is_not_sdk_owned(backend):
    # A caller-supplied pre-built wallet stays caller-owned (the client must not close it).
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    _priv, url = backend
    wallet = make_remote_wallet(url, API_KEY, WALLET_ID)
    cfg = AlloraWalletConfig(wallet=wallet)
    assert cfg._sdk_owned is False


def test_from_env_closes_wallet_if_validation_fails(backend, monkeypatch):
    # If __post_init__ validation fails after make_remote_wallet built a RemoteWallet, from_env must
    # close that wallet's HTTP session (the caller never receives a handle to close it itself).
    from allora_sdk.rpc_client.config import AlloraWalletConfig
    from allora_sdk.rpc_client.remote_signer import RemoteWallet

    _priv, url = backend
    monkeypatch.setenv("FORGE_API_KEY", API_KEY)
    monkeypatch.setenv("FORGE_SIGNING_WALLET_ID", WALLET_ID)
    monkeypatch.setenv("FORGE_BACKEND_URL", url)
    # A cosmos1 granter against the allo signing wallet fails the HRP check in __post_init__.
    monkeypatch.setenv(
        "FORGE_MASTER_GRANTER_ADDRESS", str(Address(PrivateKey().public_key, "cosmos"))
    )

    closed = {"called": False}
    real_close = RemoteWallet.close

    def spy(self):
        closed["called"] = True
        return real_close(self)

    monkeypatch.setattr(RemoteWallet, "close", spy)
    with pytest.raises(ValueError, match="HRP"):
        AlloraWalletConfig.from_env()
    assert closed["called"] is True


def test_from_env_rejects_empty_address_prefix(monkeypatch):
    # `export ADDRESS_PREFIX=` (accidentally unset) must fail loudly at config time, not as a
    # confusing downstream HRP-mismatch. The check runs before credential resolution.
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    monkeypatch.setenv("ADDRESS_PREFIX", "  ")
    with pytest.raises(ValueError, match="ADDRESS_PREFIX"):
        AlloraWalletConfig.from_env()


def test_from_env_rejects_uppercase_address_prefix(monkeypatch):
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    monkeypatch.setenv("ADDRESS_PREFIX", "ALLO")
    with pytest.raises(ValueError, match="ADDRESS_PREFIX"):
        AlloraWalletConfig.from_env()


def test_from_env_rejects_non_ascii_address_prefix(monkeypatch):
    # str.isalpha() accepts non-ASCII letters (e.g. "allø"), but a BIP-173 HRP is ASCII; the prefix
    # check must reject a non-ASCII value rather than let it produce a malformed address downstream.
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    monkeypatch.setenv("ADDRESS_PREFIX", "allø")  # "allø"
    with pytest.raises(ValueError, match="ADDRESS_PREFIX"):
        AlloraWalletConfig.from_env()


def test_from_env_accepts_and_strips_valid_address_prefix(monkeypatch):
    # A valid lowercase HRP is accepted; stray YAML whitespace is stripped.
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    monkeypatch.setenv("ADDRESS_PREFIX", " cosmos ")
    monkeypatch.setenv("PRIVATE_KEY", PrivateKey().private_key_hex)
    cfg = AlloraWalletConfig.from_env()
    assert cfg.prefix == "cosmos"


def test_from_env_reads_fee_granter_canonical(backend, monkeypatch):
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    _priv, url = backend
    granter = str(Address(PrivateKey().public_key, "allo"))
    monkeypatch.setenv("FORGE_API_KEY", API_KEY)
    monkeypatch.setenv("FORGE_SIGNING_WALLET_ID", WALLET_ID)
    monkeypatch.setenv("FORGE_BACKEND_URL", url)
    monkeypatch.setenv("FORGE_MASTER_GRANTER_ADDRESS", granter)

    cfg = AlloraWalletConfig.from_env()
    assert cfg.fee_granter == granter


def test_from_env_reads_fee_granter_legacy_alias_warns(backend, monkeypatch):
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    _priv, url = backend
    granter = str(Address(PrivateKey().public_key, "allo"))
    monkeypatch.setenv("FORGE_API_KEY", API_KEY)
    monkeypatch.setenv("FORGE_SIGNING_WALLET_ID", WALLET_ID)
    monkeypatch.setenv("FORGE_BACKEND_URL", url)
    monkeypatch.delenv("FORGE_MASTER_GRANTER_ADDRESS", raising=False)
    monkeypatch.setenv("FEE_GRANTER", granter)  # deprecated alias, still honored

    with pytest.warns(DeprecationWarning, match="FORGE_MASTER_GRANTER_ADDRESS"):
        cfg = AlloraWalletConfig.from_env()
    assert cfg.fee_granter == granter


def test_from_env_fee_granter_canonical_takes_precedence(backend, monkeypatch):
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    _priv, url = backend
    canonical = str(Address(PrivateKey().public_key, "allo"))
    legacy = str(Address(PrivateKey().public_key, "allo"))
    monkeypatch.setenv("FORGE_API_KEY", API_KEY)
    monkeypatch.setenv("FORGE_SIGNING_WALLET_ID", WALLET_ID)
    monkeypatch.setenv("FORGE_BACKEND_URL", url)
    monkeypatch.setenv("FORGE_MASTER_GRANTER_ADDRESS", canonical)
    monkeypatch.setenv("FEE_GRANTER", legacy)

    cfg = AlloraWalletConfig.from_env()
    assert cfg.fee_granter == canonical


def test_read_fee_granter_empty_canonical_does_not_shadow_legacy(monkeypatch):
    # An empty `export FORGE_MASTER_GRANTER_ADDRESS=` must be treated as unset, not shadow a valid
    # FEE_GRANTER alias (which would otherwise fail validation with a confusing empty-string error).
    from allora_sdk.rpc_client.config import _read_fee_granter

    legacy = str(Address(PrivateKey().public_key, "allo"))
    monkeypatch.setenv("FORGE_MASTER_GRANTER_ADDRESS", "")
    monkeypatch.setenv("FEE_GRANTER", legacy)
    with pytest.warns(DeprecationWarning, match="FORGE_MASTER_GRANTER_ADDRESS"):
        assert _read_fee_granter("") == legacy


def test_read_fee_granter_all_empty_returns_none(monkeypatch):
    from allora_sdk.rpc_client.config import _read_fee_granter

    monkeypatch.setenv("FORGE_MASTER_GRANTER_ADDRESS", "")
    monkeypatch.setenv("FEE_GRANTER", "")
    assert _read_fee_granter("") is None


def test_from_env_empty_backend_url_falls_back_to_default(monkeypatch):
    # An empty `export FORGE_BACKEND_URL=` must fall back to the public backend rather than surface
    # as a confusing "must have a hostname" failure.
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    monkeypatch.setenv("FORGE_API_KEY", API_KEY)
    monkeypatch.delenv("FORGE_SIGNING_WALLET_ID", raising=False)
    monkeypatch.setenv("FORGE_BACKEND_URL", "")
    for name in (
        "PRIVATE_KEY",
        "MNEMONIC",
        "MNEMONIC_FILE",
        "FEE_GRANTER",
        "FORGE_MASTER_GRANTER_ADDRESS",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = AlloraWalletConfig.from_env()
    assert cfg.forge_backend_url == "https://forge.allora.network"


def test_deferred_managed_config_validates_backend_url_eagerly():
    # A deferred managed-custody config (forge_api_key only) must reject an insecure cleartext-http
    # backend URL at construction, not later at provision time (the API key would otherwise leak).
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    with pytest.raises(ValueError, match="https"):
        AlloraWalletConfig(
            forge_api_key="forge_sk_test", forge_backend_url="http://evil.example"
        )


def test_fee_granter_invalid_bech32_rejected():
    # A typo'd granter must fail at config time, not per-transaction at broadcast time.
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    with pytest.raises(ValueError, match="invalid fee_granter"):
        AlloraWalletConfig(private_key="ab" * 32, fee_granter="allo1notarealaddress")


def test_fee_granter_wrong_payload_length_rejected():
    # A bech32 string with a valid checksum but the wrong decoded payload length (not the
    # 20 bytes of a cosmos account address) must be rejected. cosmpy's Address(str) only
    # verifies the checksum, so without an explicit length check this would slip through.
    from allora_sdk.rpc_client.config import AlloraWalletConfig
    from cosmpy.crypto.address import _to_bech32

    short = _to_bech32("allo", bytes(10))  # checksum-valid, 10-byte payload (not 20)
    with pytest.raises(ValueError, match="20-byte"):
        AlloraWalletConfig(private_key="ab" * 32, fee_granter=short)


def test_fee_granter_cross_hrp_rejected():
    # An allo1 signing wallet paired with a cosmos1 granter passes bech32 parsing but can never
    # match an on-chain feegrant, so it must be rejected eagerly.
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    cosmos_granter = str(Address(PrivateKey().public_key, "cosmos"))
    with pytest.raises(ValueError, match="HRP"):
        AlloraWalletConfig(private_key="ab" * 32, fee_granter=cosmos_granter)


def test_fee_granter_uppercase_bech32_accepted():
    # BIP-173 bech32 is case-insensitive: an all-uppercase ALLO1... granter encodes the same address
    # as its lowercase form, so the HRP check must accept it (it must not be rejected as cross-HRP).
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    granter = str(Address(PrivateKey().public_key, "allo")).upper()
    cfg = AlloraWalletConfig(private_key="ab" * 32, fee_granter=granter)
    assert cfg.fee_granter == granter


def test_forge_client_close_respects_session_ownership():
    # A self-created session is closed by close() (idempotently); a caller-injected session
    # is left open because the caller owns its lifecycle.
    import requests
    from unittest.mock import MagicMock
    from allora_sdk.rpc_client.remote_signer import ForgeBackendClient

    owned = ForgeBackendClient("https://forge.invalid", API_KEY)
    assert owned._owns_session is True
    owned._session = MagicMock(spec=requests.Session)
    owned.close()
    owned.close()
    assert owned._session.close.call_count == 2  # idempotent, both reach session.close

    injected_session = MagicMock(spec=requests.Session)
    injected = ForgeBackendClient("https://forge.invalid", API_KEY, session=injected_session)
    assert injected._owns_session is False
    injected.close()
    injected_session.close.assert_not_called()


def test_http_pool_sized_to_signing_pool(monkeypatch):
    # The connection pool must not cap below ALLORA_SIGNING_POOL_SIZE, or signing threads
    # fan out but serialize on urllib3's default 10-connection pool.
    from allora_sdk.rpc_client.remote_signer import ForgeBackendClient

    monkeypatch.delenv("ALLORA_SIGNING_POOL_SIZE", raising=False)
    owned = ForgeBackendClient("https://forge.invalid", API_KEY)
    adapter = owned._session.get_adapter("https://forge.invalid")
    assert adapter._pool_maxsize >= 10  # default floor

    monkeypatch.setenv("ALLORA_SIGNING_POOL_SIZE", "32")
    raised = ForgeBackendClient("https://forge.invalid", API_KEY)
    raised_adapter = raised._session.get_adapter("https://forge.invalid")
    assert raised_adapter._pool_maxsize == 32
    assert raised_adapter._pool_connections == 32


def test_remote_wallet_close_delegates(backend):
    # RemoteWallet.close releases the backend session it owns; idempotent and non-raising.
    _priv, url = backend
    wallet = make_remote_wallet(url, API_KEY, WALLET_ID)
    wallet.close()
    wallet.close()


def test_wallet_prefix_conflict_rejected():
    # An explicit, non-default prefix that disagrees with the pre-built wallet's actual HRP
    # must raise, not be silently overwritten (which would mask a real misconfiguration).
    from allora_sdk.rpc_client.config import AlloraWalletConfig
    from cosmpy.aerial.wallet import LocalWallet

    allo_wallet = LocalWallet(PrivateKey(), prefix="allo")
    with pytest.raises(ValueError, match="disagrees"):
        AlloraWalletConfig(wallet=allo_wallet, prefix="cosmos")


def test_wallet_default_prefix_aligns_to_wallet_hrp():
    # With prefix left at the default the caller expressed no opinion, so the config
    # silently adopts the wallet's actual HRP rather than forcing "allo".
    from allora_sdk.rpc_client.config import AlloraWalletConfig
    from cosmpy.aerial.wallet import LocalWallet

    cosmos_wallet = LocalWallet(PrivateKey(), prefix="cosmos")
    cfg = AlloraWalletConfig(wallet=cosmos_wallet)
    assert cfg.prefix == "cosmos"


def test_clear_association(backend):
    from allora_sdk.rpc_client.remote_signer import ForgeBackendClient

    _priv, url = backend
    client = ForgeBackendClient(url, API_KEY)
    # A 2xx response returns without raising (the topic binding was released).
    client.clear_association(WALLET_ID)


def test_clear_association_accepts_204_no_content():
    from allora_sdk.rpc_client.remote_signer import ForgeBackendClient

    # A successful unbind may legitimately return 204 No Content (empty body). _request must
    # treat an empty 2xx body as success rather than failing the JSON-object parse.
    class NoContentHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.send_response(204)
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), NoContentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        ForgeBackendClient(url, API_KEY).clear_association(WALLET_ID)  # must not raise
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_revoke_wallet(backend):
    from allora_sdk.rpc_client.remote_signer import ForgeBackendClient

    _priv, url = backend
    client = ForgeBackendClient(url, API_KEY)
    # A 2xx response to DELETE /api/v1/signing-wallets/:id returns without raising.
    client.revoke_wallet(WALLET_ID)


def test_revoke_wallet_rejects_non_uuid():
    # revoke is destructive (DELETE), so a non-UUID wallet_id fails fast locally (parity with
    # RemoteWallet's uuid guard) rather than issuing a DELETE that 404s.
    from allora_sdk.rpc_client.remote_signer import ForgeBackendClient

    client = ForgeBackendClient("https://forge.invalid", API_KEY)
    with pytest.raises(WalletConfigError, match="UUID"):
        client.revoke_wallet("not-a-uuid")


def test_provision_remote_wallet(backend):
    from allora_sdk.rpc_client.remote_signer import provision_remote_wallet

    priv, url = backend
    wallet = provision_remote_wallet(url, API_KEY, topic_id=42)
    assert str(wallet.address()) == str(Address(priv.public_key, "allo"))
    # The provisioned wallet signs through the same backend.
    sig = wallet.signer().sign(b"signdoc bytes")
    assert priv.public_key.verify(b"signdoc bytes", sig)


def test_provision_retries_transient_5xx(monkeypatch):
    # provision_wallet is idempotent (get-or-create), so a transient 503 during worker startup
    # must be retried rather than failing the worker (clear_association stays non-retried).
    from allora_sdk.rpc_client.remote_signer import provision_remote_wallet

    priv = PrivateKey()
    pub_hex = priv.public_key.public_key_bytes.hex()
    address = str(Address(priv.public_key, "allo"))
    state = {"posts": 0}

    class FlakyHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            state["posts"] += 1
            if state["posts"] == 1:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = json.dumps({"id": WALLET_ID, "address": address, "pubkey": pub_hex}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    monkeypatch.setattr("allora_sdk.rpc_client.remote_signer.time.sleep", lambda *_: None)
    server = HTTPServer(("127.0.0.1", 0), FlakyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        wallet = provision_remote_wallet(url, API_KEY, topic_id=42)
        assert str(wallet.address()) == address
        assert state["posts"] == 2  # first 503 retried, second 200 succeeded
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_provision_does_not_retry_4xx(monkeypatch):
    # A permanent client error (e.g. bad api key -> 403) must fail fast, not retry.
    from allora_sdk.rpc_client.remote_signer import ForgeBackendError, provision_remote_wallet

    state = {"posts": 0}

    class ForbiddenHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            state["posts"] += 1
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()

    monkeypatch.setattr("allora_sdk.rpc_client.remote_signer.time.sleep", lambda *_: None)
    server = HTTPServer(("127.0.0.1", 0), ForbiddenHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(ForgeBackendError, match="403"):
            provision_remote_wallet(url, API_KEY, topic_id=42)
        assert state["posts"] == 1  # 4xx fails fast, no retry
    finally:
        server.shutdown()
        thread.join(timeout=2)


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


def test_remote_wallet_discovers_master_granter_from_wallet_info(backend):
    # The backend reports master_granter on wallet-info; the RemoteWallet captures it as the
    # fee_granter fallback so a worker can subsidize gas without explicit config.
    _priv, url = backend
    wallet = make_remote_wallet(url, API_KEY, WALLET_ID)
    assert wallet.fee_granter == MASTER_GRANTER


def test_provision_remote_wallet_discovers_master_granter(backend):
    # The provision response carries master_granter; it is threaded onto the wallet without a
    # second (blocking) wallet-info fetch.
    from allora_sdk.rpc_client.remote_signer import provision_remote_wallet

    _priv, url = backend
    wallet = provision_remote_wallet(url, API_KEY, topic_id=42)
    assert wallet.fee_granter == MASTER_GRANTER


def test_public_key_hex_shortcut_has_no_discovered_granter():
    # The async shortcut skips wallet-info, so there is no master_granter to discover.
    priv = PrivateKey()
    pub_hex = priv.public_key.public_key_bytes.hex()
    address = str(Address(priv.public_key, "allo"))
    wallet = make_remote_wallet(
        "https://forge.invalid",
        API_KEY,
        WALLET_ID,
        public_key_hex=pub_hex,
        address=address,
    )
    assert wallet.fee_granter is None


def test_resolve_fee_granter_precedence(backend):
    # env/explicit config overrides the discovered value; the discovered master_granter is the
    # fallback when no fee_granter is configured.
    from allora_sdk.rpc_client.config import AlloraWalletConfig
    from allora_sdk.worker.utils import init_worker_wallet, resolve_fee_granter

    _priv, url = backend
    cfg = AlloraWalletConfig(forge_api_key=API_KEY, forge_backend_url=url)
    wallet = init_worker_wallet(cfg, topic_id=7)
    assert resolve_fee_granter(cfg, wallet) == MASTER_GRANTER

    override = str(Address(PrivateKey().public_key, "allo"))
    cfg_override = AlloraWalletConfig(
        forge_api_key=API_KEY, forge_backend_url=url, fee_granter=override
    )
    assert resolve_fee_granter(cfg_override, wallet) == override


def test_resolve_fee_granter_local_wallet_has_no_discovery():
    # A LocalWallet carries no discovered granter; resolve falls through to the config value.
    from allora_sdk.rpc_client.config import AlloraWalletConfig
    from allora_sdk.worker.utils import resolve_fee_granter
    from cosmpy.aerial.wallet import LocalWallet

    local = LocalWallet(PrivateKey(), prefix="allo")
    assert resolve_fee_granter(AlloraWalletConfig(wallet=local), local) is None

    granter = str(Address(PrivateKey().public_key, "allo"))
    cfg = AlloraWalletConfig(wallet=local, fee_granter=granter)
    assert resolve_fee_granter(cfg, local) == granter


def test_forge_api_key_rejects_local_credentials():
    # Managed (Privy) custody must be the sole credential source. Combined with a local key the
    # worker would silently provision and sign with a remote wallet (wrong worker address), so the
    # ambiguous config must be rejected at construction rather than producing a wrong custody path.
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    with pytest.raises(ValueError, match="cannot be combined"):
        AlloraWalletConfig(forge_api_key=API_KEY, private_key="ab" * 32)


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


def test_from_env_deferred_managed_conflicting_credentials_rejected(monkeypatch):
    # The FORGE_API_KEY-only (deferred-provisioning) path must apply the same single-source
    # guard as the wallet-id path, otherwise a stale PRIVATE_KEY/MNEMONIC/MNEMONIC_FILE would be
    # silently ignored while signing switched to managed Forge custody.
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    monkeypatch.setenv("FORGE_API_KEY", API_KEY)
    monkeypatch.delenv("FORGE_SIGNING_WALLET_ID", raising=False)
    monkeypatch.setenv("MNEMONIC", "test test test")
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


def test_public_key_hex_shortcut_empty_address_still_cross_checked():
    # A caller-supplied address="" on the public_key_hex shortcut must not silently skip the
    # pubkey<->address cross-check (the docstring promises the check when address is passed).
    # An empty string is a provided (mismatching) address, not "absent".
    priv = PrivateKey()
    pub_hex = priv.public_key.public_key_bytes.hex()
    with pytest.raises(WalletConfigError, match="does not match"):
        make_remote_wallet(
            "https://forge.invalid",
            "forge_sk_test",
            WALLET_ID,
            public_key_hex=pub_hex,
            address="",
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
            self.send_header("Content-Type", "application/json")
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
            self.send_header("Content-Type", "application/json")
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


def test_captive_portal_html_response_rejected_with_content_type():
    # A 200 text/html page (captive portal / auth proxy / misconfigured CDN) must surface a
    # clear Content-Type error, and every request must send Accept: application/json so a
    # content-negotiating backend returns JSON (parity with allora-sdk-go / allora-sdk-ts).
    seen = {}

    class HtmlHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen["accept"] = self.headers.get("Accept")
            body = b"<html>login</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), HtmlHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(ForgeBackendError, match="Content-Type"):
            make_remote_wallet(url, API_KEY, WALLET_ID)
        assert seen["accept"] == "application/json"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_signing_wallet_info_models_full_contract():
    # forge-v2's wallet-info DTO returns evm_address / label / topic_id / worker_label /
    # created_at alongside id/address/pubkey. privy_wallet_id is tagged json:"-" server-side
    # (never on the wire), so it is intentionally not modeled; any unknown field is tolerated.
    info = SigningWalletInfo.model_validate(
        {
            "id": WALLET_ID,
            "address": "allo1xyz",
            "pubkey": "ab" * 33,
            "evm_address": "0xabc",
            "label": "my-wallet",
            "topic_id": 7,
            "worker_label": "btc-inferer",
            "created_at": "2024-01-02T03:04:05Z",
            "master_granter": "allo1master",
            "privy_wallet_id": "ignored-never-on-wire",
            "some_future_field": "ignored",
        }
    )
    assert info.evm_address == "0xabc"
    assert info.label == "my-wallet"
    assert info.topic_id == 7
    assert info.worker_label == "btc-inferer"
    assert info.created_at == "2024-01-02T03:04:05Z"
    assert info.master_granter == "allo1master"
    # privy_wallet_id is no longer modeled (server never emits it).
    assert not hasattr(info, "privy_wallet_id")


def test_non_https_backend_url_rejected():
    # Plain http:// to a non-loopback host would leak the API key in cleartext.
    with pytest.raises(ValueError, match="https"):
        make_remote_wallet("http://forge.example.com", API_KEY, WALLET_ID)


def test_backend_url_with_userinfo_rejected():
    # Embedded user:password@host is sent as a Basic auth header alongside the API key.
    with pytest.raises(ValueError, match="userinfo"):
        make_remote_wallet("https://user:pass@forge.example.com", API_KEY, WALLET_ID)


def test_backend_url_with_query_or_fragment_rejected():
    # A query string / fragment is URL-encoded into every request path.
    with pytest.raises(ValueError, match="query string or fragment"):
        make_remote_wallet("https://forge.example.com?x=1", API_KEY, WALLET_ID)
    with pytest.raises(ValueError, match="query string or fragment"):
        make_remote_wallet("https://forge.example.com#frag", API_KEY, WALLET_ID)


def test_backend_url_without_hostname_rejected():
    # A scheme-only URL would otherwise fail with an opaque ConnectionError at first use.
    with pytest.raises(ValueError, match="hostname"):
        make_remote_wallet("https://", API_KEY, WALLET_ID)


def test_backend_url_with_path_rejected():
    # A non-root path is prepended to every request URL (e.g. https://host/api ->
    # /api/api/v1/...), so signing 404s. Parity with allora-sdk-go's requireSecureBackend.
    with pytest.raises(ValueError, match="path"):
        make_remote_wallet("https://forge.example.com/api", API_KEY, WALLET_ID)
    # A bare trailing slash is the root path and stays accepted.
    from allora_sdk.rpc_client.remote_signer import _validate_backend_url

    _validate_backend_url("https://forge.example.com/")


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
            self.send_header("Content-Type", "application/json")
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


def test_sign_response_missing_pubkey_echo_rejected():
    # Fail closed: a sign response that omits the pubkey echo must be rejected, matching
    # allora-sdk-ts. Otherwise a backend could simply drop the field to dodge the
    # rotation/mis-route check.
    priv = PrivateKey()
    pub_hex = priv.public_key.public_key_bytes.hex()
    address = str(Address(priv.public_key, "allo"))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
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
            # Valid signature over the payload, but no pubkey echo field at all.
            self._send({"signature": sig.hex()})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        wallet = make_remote_wallet(url, API_KEY, WALLET_ID)
        with pytest.raises(ForgeBackendError, match="omitted the pubkey echo"):
            wallet.signer().sign(b"cosmos signdoc bytes")
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_wallet_id_normalized_to_canonical(backend):
    # A non-canonical UUID (braced form) must be normalized to the canonical dashed form so it
    # matches the backend's canonical id and is URL-encoded canonically. Before normalization the
    # braced form spuriously failed the wallet-info id cross-check.
    priv, url = backend
    wallet = make_remote_wallet(url, API_KEY, "{" + WALLET_ID + "}")
    assert str(wallet.address()) == str(Address(priv.public_key, "allo"))
    # The signer carries the canonical (dashed, unbraced) wallet_id.
    assert wallet.signer()._wallet_id == WALLET_ID


def test_wallet_config_repr_hides_secrets():
    # The auto-generated dataclass repr must not leak signing credentials (logs/tracebacks).
    from allora_sdk.rpc_client.config import AlloraWalletConfig

    api_key_repr = repr(AlloraWalletConfig(forge_api_key="forge_sk_SECRET"))
    assert "forge_sk_SECRET" not in api_key_repr
    assert "ab" * 32 not in repr(AlloraWalletConfig(private_key="ab" * 32))
    assert "abandon" not in repr(AlloraWalletConfig(mnemonic="abandon " * 12))


def test_remote_wallet_rejects_invalid_discovered_granter():
    # Defense-in-depth: a backend-supplied master_granter with the wrong HRP (cosmos1 for an
    # allo wallet) must be rejected at the RemoteWallet boundary, not stored unvalidated.
    from allora_sdk.rpc_client.remote_signer import RemoteWallet

    priv = PrivateKey()
    pub_hex = priv.public_key.public_key_bytes.hex()
    address = str(Address(priv.public_key, "allo"))
    cosmos_granter = str(Address(PrivateKey().public_key, "cosmos"))
    with pytest.raises(WalletConfigError, match="master_granter"):
        RemoteWallet(
            "https://forge.invalid",
            API_KEY,
            WALLET_ID,
            public_key_hex=pub_hex,
            address=address,
            fee_granter=cosmos_granter,
        )
