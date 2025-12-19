"""
Pytest configuration for RPC client tests.

This conftest prevents importing modules with Python 3.12+ syntax when running
tests on Python 3.10/3.11.
"""

import sys
from unittest import mock

# Mock modules that use Python 3.12+ syntax
# These modules use type parameter syntax and type aliases that require Python 3.12+
if sys.version_info < (3, 12):
    # Mock worker modules
    sys.modules['allora_sdk.worker'] = mock.MagicMock()
    sys.modules['allora_sdk.worker.worker'] = mock.MagicMock()

    # Mock the top-level allora_sdk module to prevent importing worker
    # We need to do this before any imports happen
    import importlib.util
    spec = importlib.util.find_spec("allora_sdk")
    if spec and spec.origin:
        # Create a minimal mock that only exports rpc_client
        mock_module = type(sys)('allora_sdk')
        mock_module.__file__ = spec.origin
        mock_module.__path__ = spec.submodule_search_locations
        sys.modules['allora_sdk'] = mock_module
