#!/usr/bin/env python3
"""
Debug script to investigate TxFee to_proto issue with correct parameters.
"""

import traceback
from cosmpy.aerial.tx import TxFee
from cosmpy.aerial.coins import Coin

def test_txfee_to_proto():
    """Test TxFee.to_proto() method to see what's failing."""
    print("🔍 Testing TxFee.to_proto() method with correct parameters...")
    
    try:
        # Create a fee object like in tx_manager.py (corrected)
        fee_coin = Coin(amount=1000, denom="uallo")
        fee_list = [fee_coin]
        gas_limit = 200000
        
        print(f"Fee coin: {fee_coin}")
        print(f"Fee coin type: {type(fee_coin)}")
        print(f"Fee list: {fee_list}")
        print(f"Gas limit: {gas_limit}")
        
        # Create TxFee object
        tx_fee = TxFee(amount=fee_list, gas_limit=gas_limit)
        print(f"✅ TxFee object created: {tx_fee}")
        print(f"TxFee type: {type(tx_fee)}")
        
        # Check if to_proto method exists
        if hasattr(tx_fee, 'to_proto'):
            print("✅ TxFee has to_proto method")
            try:
                proto_result = tx_fee.to_proto()
                print(f"✅ to_proto() succeeded: {proto_result}")
                print(f"Proto result type: {type(proto_result)}")
            except Exception as e:
                print(f"❌ to_proto() failed: {e}")
                print(f"Exception type: {type(e)}")
                traceback.print_exc()
        else:
            print("❌ TxFee does not have to_proto method")
            print(f"Available methods: {[m for m in dir(tx_fee) if not m.startswith('_')]}")
            
    except ImportError as e:
        print(f"❌ Import error: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        print(f"Exception type: {type(e)}")
        traceback.print_exc()

if __name__ == "__main__":
    test_txfee_to_proto()