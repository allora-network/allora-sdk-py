#!/usr/bin/env python3
"""
Test that TxManager correctly calculates fees using feemarket gas prices.
"""
import asyncio
from unittest.mock import Mock, MagicMock
from decimal import Decimal
from allora_sdk.rpc_client.tx_manager import TxManager
from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.protos.feemarket.feemarket.v1 import GasPriceResponse
from allora_sdk.protos.cosmos.base.v1beta1 import DecCoin

async def test_fee_calculation_with_dynamic_prices():
    """Test that fees are calculated correctly with cosmos.Dec format gas prices."""

    # Setup mock clients
    wallet = Mock()
    tx_client = Mock()
    auth_client = Mock()
    bank_client = Mock()

    # Mock feemarket client returning price in cosmos.Dec format (18 decimals)
    feemarket_client = Mock()
    mock_response = GasPriceResponse(
        price=DecCoin(
            denom="uallo",
            amount="10000000000000000000"  # 10.0 in cosmos.Dec format
        )
    )
    feemarket_client.gas_price = Mock(return_value=mock_response)

    # Create config with dynamic pricing enabled
    config = AlloraNetworkConfig.testnet()
    config.use_dynamic_gas_price = True
    config.congestion_aware_fees = False

    # Create TxManager
    tx_manager = TxManager(
        wallet=wallet,
        tx_client=tx_client,
        auth_client=auth_client,
        bank_client=bank_client,
        feemarket_client=feemarket_client,
        config=config,
    )

    # Test _get_current_gas_price
    print("Testing _get_current_gas_price()...")
    gas_price = await tx_manager._get_current_gas_price()
    print(f"  ✓ Gas price: {gas_price}")
    assert gas_price == 10.0, f"Expected 10.0, got {gas_price}"

    # Test _calculate_optimal_fee
    print("\nTesting _calculate_optimal_fee()...")

    gas_limit = 200000

    # ECO tier (1.0x multiplier)
    eco_fee = await tx_manager._calculate_optimal_fee(gas_limit, 1.0)
    print(f"  ✓ ECO fee: {eco_fee.amount} {eco_fee.denom}")
    assert eco_fee.amount == 2000000, f"Expected 2000000, got {eco_fee.amount}"
    assert eco_fee.denom == "uallo"

    # STANDARD tier (1.5x multiplier)
    standard_fee = await tx_manager._calculate_optimal_fee(gas_limit, 1.5)
    print(f"  ✓ STANDARD fee: {standard_fee.amount} {standard_fee.denom}")
    assert standard_fee.amount == 3000000, f"Expected 3000000, got {standard_fee.amount}"
    assert standard_fee.denom == "uallo"

    # PRIORITY tier (2.5x multiplier)
    priority_fee = await tx_manager._calculate_optimal_fee(gas_limit, 2.5)
    print(f"  ✓ PRIORITY fee: {priority_fee.amount} {priority_fee.denom}")
    assert priority_fee.amount == 5000000, f"Expected 5000000, got {priority_fee.amount}"
    assert priority_fee.denom == "uallo"

    print("\n✅ All tests passed!")

if __name__ == "__main__":
    asyncio.run(test_fee_calculation_with_dynamic_prices())
