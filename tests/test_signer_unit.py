"""
Unit tests for Signer Protocol and implementations.

Tests cover:
- LocalWalletSigner initialization and signing
- VaultSigner with mocked HTTP calls
- AlloraWalletConfig.get_signer() priority
- Protocol compliance

Note: These tests use direct file imports to avoid triggering the main SDK
__init__.py which requires generated protobuf files. This allows running
tests without the full proto generation step.
"""

import sys
import os
import importlib.util

# Direct file loading to bypass __init__.py files that import protos
def _load_module(name: str, path: str):
    """Load a module directly from file path without triggering package __init__.py"""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

# Get the src directory path
_src_dir = os.path.join(os.path.dirname(__file__), '..', 'src')

# Pre-populate sys.modules to prevent automatic loading of __init__.py files
sys.modules['allora_sdk'] = type(sys)('allora_sdk')
sys.modules['allora_sdk'].__path__ = [os.path.join(_src_dir, 'allora_sdk')]
sys.modules['allora_sdk.rpc_client'] = type(sys)('allora_sdk.rpc_client')
sys.modules['allora_sdk.rpc_client'].__path__ = [os.path.join(_src_dir, 'allora_sdk', 'rpc_client')]

# Load modules directly from files
_signer_module = _load_module(
    'allora_sdk.rpc_client.signer',
    os.path.join(_src_dir, 'allora_sdk', 'rpc_client', 'signer.py')
)
_config_module = _load_module(
    'allora_sdk.rpc_client.config',
    os.path.join(_src_dir, 'allora_sdk', 'rpc_client', 'config.py')
)

import pytest
from unittest.mock import Mock, patch

# Extract classes from loaded modules
Signer = _signer_module.Signer
LocalWalletSigner = _signer_module.LocalWalletSigner
VaultSigner = _signer_module.VaultSigner
AlloraWalletConfig = _config_module.AlloraWalletConfig


# Test constants
TEST_PRIVATE_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
TEST_MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
TEST_DIGEST = bytes.fromhex("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


class TestLocalWalletSigner:
    """Tests for LocalWalletSigner"""

    def test_init_with_private_key(self):
        """Should initialize from private key with correct prefix"""
        signer = LocalWalletSigner(private_key=TEST_PRIVATE_KEY)
        assert signer.address.startswith("allo")
        assert len(signer.public_key_hex) == 66  # Compressed pubkey

    def test_init_with_private_key_custom_prefix(self):
        """Should respect custom prefix"""
        signer = LocalWalletSigner(private_key=TEST_PRIVATE_KEY, prefix="fetch")
        assert signer.address.startswith("fetch")

    def test_init_with_mnemonic(self):
        """Should initialize from mnemonic"""
        signer = LocalWalletSigner(mnemonic=TEST_MNEMONIC)
        assert signer.address.startswith("allo")

    def test_init_with_wallet(self):
        """Should accept pre-initialized LocalWallet"""
        from cosmpy.aerial.wallet import LocalWallet, PrivateKey
        pk = PrivateKey(bytes.fromhex(TEST_PRIVATE_KEY))
        wallet = LocalWallet(pk, prefix="allo")

        signer = LocalWalletSigner(wallet=wallet)
        assert signer.address == str(wallet.address())

    def test_init_no_credentials_raises(self):
        """Should raise ValueError when no credentials provided"""
        with pytest.raises(ValueError, match="Must provide"):
            LocalWalletSigner()

    def test_sign_digest(self):
        """Should produce valid signature"""
        signer = LocalWalletSigner(private_key=TEST_PRIVATE_KEY)
        signature = signer.sign_digest(TEST_DIGEST)
        assert isinstance(signature, bytes)
        assert len(signature) == 64  # secp256k1 signature

    def test_wallet_property(self):
        """Should expose underlying LocalWallet"""
        signer = LocalWalletSigner(private_key=TEST_PRIVATE_KEY)
        assert signer.wallet is not None
        assert str(signer.wallet.address()) == signer.address


class TestVaultSigner:
    """Tests for VaultSigner with mocked HTTP"""

    def test_init(self):
        """Should store configuration"""
        signer = VaultSigner(
            backend_url="http://localhost:8080",
            worker_id="test-worker-id",
            auth_token="test-token"
        )
        assert signer._backend_url == "http://localhost:8080"
        assert signer._worker_id == "test-worker-id"

    def test_init_strips_trailing_slash(self):
        """Should strip trailing slash from backend URL"""
        signer = VaultSigner(
            backend_url="http://localhost:8080/",
            worker_id="test-id",
            auth_token="token"
        )
        assert signer._backend_url == "http://localhost:8080"

    @patch('httpx.get')
    def test_get_identity_lazy_loads(self, mock_get):
        """Should lazy-load identity on first access"""
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: {
                "publicKey": "02abc123",
                "address": "allo1xyz"
            }
        )
        mock_get.return_value.raise_for_status = Mock()

        signer = VaultSigner(
            backend_url="http://localhost:8080",
            worker_id="test-id",
            auth_token="token"
        )

        # Should not call until accessed
        mock_get.assert_not_called()

        # Access triggers call
        assert signer.address == "allo1xyz"
        mock_get.assert_called_once()

        # Second access uses cache
        assert signer.public_key_hex == "02abc123"
        mock_get.assert_called_once()  # Still just one call

    @patch('httpx.post')
    def test_sign_digest(self, mock_post):
        """Should call backend API for signing"""
        mock_post.return_value = Mock(
            status_code=200,
            json=lambda: {
                "success": True,
                "signatureHex": "deadbeef" * 16  # 64 bytes hex
            }
        )
        mock_post.return_value.raise_for_status = Mock()

        signer = VaultSigner(
            backend_url="http://localhost:8080",
            worker_id="test-id",
            auth_token="token"
        )

        signature = signer.sign_digest(TEST_DIGEST)

        assert isinstance(signature, bytes)
        assert len(signature) == 64
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "sign-bundle" in call_args[0][0]

    @patch('httpx.post')
    def test_sign_digest_supports_snake_case_response(self, mock_post):
        """Should handle snake_case response fields"""
        mock_post.return_value = Mock(
            status_code=200,
            json=lambda: {
                "success": True,
                "signature_hex": "abcd" * 32  # 64 bytes hex
            }
        )
        mock_post.return_value.raise_for_status = Mock()

        signer = VaultSigner(
            backend_url="http://localhost:8080",
            worker_id="test-id",
            auth_token="token"
        )

        signature = signer.sign_digest(TEST_DIGEST)
        assert len(signature) == 64

    @patch('httpx.post')
    def test_sign_digest_error_handling(self, mock_post):
        """Should handle signing errors"""
        mock_post.return_value = Mock(
            status_code=200,
            json=lambda: {
                "success": False,
                "error": "Worker not found"
            }
        )
        mock_post.return_value.raise_for_status = Mock()

        signer = VaultSigner(
            backend_url="http://localhost:8080",
            worker_id="test-id",
            auth_token="token"
        )

        with pytest.raises(RuntimeError, match="Worker not found"):
            signer.sign_digest(TEST_DIGEST)


class TestAlloraWalletConfigGetSigner:
    """Tests for AlloraWalletConfig.get_signer()"""

    def test_get_signer_from_private_key(self):
        """Should create LocalWalletSigner from private_key"""
        config = AlloraWalletConfig(private_key=TEST_PRIVATE_KEY)
        signer = config.get_signer()
        assert isinstance(signer, LocalWalletSigner)
        assert signer.address.startswith("allo")

    def test_get_signer_from_mnemonic(self):
        """Should create LocalWalletSigner from mnemonic"""
        config = AlloraWalletConfig(mnemonic=TEST_MNEMONIC)
        signer = config.get_signer()
        assert isinstance(signer, LocalWalletSigner)

    def test_get_signer_custom_signer_priority(self):
        """Custom signer should take precedence"""
        custom_signer = Mock(spec=Signer)
        config = AlloraWalletConfig(
            private_key=TEST_PRIVATE_KEY,  # Also provided
            signer=custom_signer           # Should take precedence
        )
        assert config.get_signer() is custom_signer

    def test_config_validation_with_signer(self):
        """Should accept signer as valid credential"""
        custom_signer = Mock(spec=Signer)
        config = AlloraWalletConfig(signer=custom_signer)
        # Should not raise
        assert config.signer is custom_signer

    def test_get_signer_respects_prefix(self):
        """Should pass prefix to LocalWalletSigner"""
        config = AlloraWalletConfig(private_key=TEST_PRIVATE_KEY, prefix="cosmos")
        signer = config.get_signer()
        assert signer.address.startswith("cosmos")


class TestSignerProtocol:
    """Tests for Signer Protocol compliance"""

    def test_local_wallet_signer_is_signer(self):
        """LocalWalletSigner should satisfy Signer protocol"""
        signer = LocalWalletSigner(private_key=TEST_PRIVATE_KEY)
        assert isinstance(signer, Signer)

    @patch('httpx.get')
    @patch('httpx.post')
    def test_vault_signer_is_signer(self, mock_post, mock_get):
        """VaultSigner should satisfy Signer protocol"""
        mock_get.return_value = Mock(
            json=lambda: {"publicKey": "02abc", "address": "allo1xyz"}
        )
        mock_get.return_value.raise_for_status = Mock()
        mock_post.return_value = Mock(
            json=lambda: {"success": True, "signatureHex": "ab" * 64}
        )
        mock_post.return_value.raise_for_status = Mock()

        signer = VaultSigner(
            backend_url="http://localhost:8080",
            worker_id="test-id",
            auth_token="token"
        )
        assert isinstance(signer, Signer)

    def test_signer_protocol_methods(self):
        """Signer protocol should define required methods"""
        # Verify the protocol has the expected attributes
        assert hasattr(Signer, 'sign_digest')
        assert hasattr(Signer, 'public_key_hex')
        assert hasattr(Signer, 'address')
