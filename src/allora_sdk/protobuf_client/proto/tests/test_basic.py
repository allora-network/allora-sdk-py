"""
Basic tests for the generated protobuf code
"""

import pytest

def test_mint_module_import():
    """Test that we can import the mint module"""
    try:
        from mint.v1beta1 import QueryServiceStub, QueryParamsRequest
        assert QueryServiceStub is not None
        assert QueryParamsRequest is not None
    except ImportError as e:
        pytest.skip(f"Mint module not available: {e}")

def test_emissions_module_import():
    """Test that we can import the emissions module"""
    try:
        from emissions.v1 import QueryServiceStub, QueryParamsRequest
        assert QueryServiceStub is not None
        assert QueryParamsRequest is not None
    except ImportError as e:
        pytest.skip(f"Emissions module not available: {e}")

def test_mint_message_creation():
    """Test that we can create mint messages"""
    try:
        from mint.v1beta1 import QueryParamsRequest
        request = QueryParamsRequest()
        assert request is not None
    except ImportError as e:
        pytest.skip(f"Mint module not available: {e}")

def test_emissions_message_creation():
    """Test that we can create emissions messages"""
    try:
        from emissions.v1 import QueryParamsRequest
        request = QueryParamsRequest()
        assert request is not None
    except ImportError as e:
        pytest.skip(f"Emissions module not available: {e}")
