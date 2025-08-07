#!/usr/bin/env python3
"""
Example: Submitting worker inferences to the Allora network

This example demonstrates how to:
1. Connect to the Allora testnet 
2. Submit inference transactions
3. Monitor transaction results
"""

import asyncio
import os
import logging
from allora_sdk.protobuf_client.client import ProtobufClient

# Enable debug logging to see transaction details
logging.basicConfig(level=logging.INFO)

async def main():
    # Get credentials from environment variables
    private_key = os.getenv('ALLORA_PRIVATE_KEY')
    mnemonic = os.getenv('ALLORA_MNEMONIC')
    
    if not private_key and not mnemonic:
        print("❌ Please set either ALLORA_PRIVATE_KEY or ALLORA_MNEMONIC environment variable")
        print("   export ALLORA_PRIVATE_KEY='your_hex_private_key'")
        print("   or")
        print("   export ALLORA_MNEMONIC='your twelve word mnemonic phrase'")
        return
    
    # Create client
    print("🚀 Connecting to Allora testnet...")
    if private_key:
        client = ProtobufClient.testnet(private_key=private_key, debug=True)
    else:
        client = ProtobufClient.testnet(mnemonic=mnemonic, debug=True)
    
    print(f"📱 Wallet address: {client.address}")
    
    # Check connection and balance
    network_info = client.get_network_info()
    if not network_info.get('connected'):
        print(f"❌ Failed to connect to network: {network_info.get('error')}")
        return
        
    print(f"🌐 Connected to: {network_info['chain_id']}")
    print(f"📊 Latest block: {network_info['latest_block']}")
    
    # Check balance
    balance = client.get_balance()
    fee_denom = client.config.fee_denom
    balance_amount = balance.get(fee_denom, 0)
    print(f"💰 Balance: {balance_amount} {fee_denom}")
    
    if balance_amount == 0:
        print("❌ Insufficient balance for transaction fees")
        print(f"   Please fund your wallet with {fee_denom} tokens")
        return
    
    # Example 1: Simple inference submission
    print("\n🧠 Example 1: Simple inference submission")
    try:
        topic_id = 13
        inference_value = "42.5"
        
        print(f"📤 Submitting inference for topic {topic_id}: {inference_value}")
        
        response = await client.transactions.submit_worker_payload(
            topic_id=topic_id,
            inference_value=inference_value,
            memo="Example inference from Python SDK"
        )
        
        if response.code == 0:
            print(f"✅ Transaction successful!")
            print(f"   Hash: {response.txhash}")
            print(f"   Gas used: {response.gas_used}")
        else:
            print(f"❌ Transaction failed (code {response.code})")
            print(f"   Error: {response.raw_log}")
            
    except Exception as e:
        print(f"❌ Error submitting inference: {e}")
    
    # Example 2: Inference with forecast elements
    print("\n🔮 Example 2: Inference with forecast elements")
    try:
        forecast_elements = [
            {"inferer": "allo1example1", "value": "43.0"},
            {"inferer": "allo1example2", "value": "42.1"}
        ]
        
        print(f"📤 Submitting inference with {len(forecast_elements)} forecast elements")
        
        response = await client.transactions.submit_worker_payload(
            topic_id=topic_id,
            inference_value="42.8",
            forecast_elements=forecast_elements,
            extra_data=b"example_metadata",
            proof="example_proof_string",
            memo="Inference with forecasts",
            gas_limit=400000
        )
        
        if response.code == 0:
            print(f"✅ Advanced transaction successful!")
            print(f"   Hash: {response.txhash}")
        else:
            print(f"❌ Advanced transaction failed (code {response.code})")
            print(f"   Error: {response.raw_log}")
            
    except Exception as e:
        print(f"❌ Error submitting advanced inference: {e}")
    
    print("\n🎉 Example completed!")
    print("\nNext steps:")
    print("- Monitor your transactions on the blockchain explorer")
    print("- Set up event subscriptions to listen for score updates")  
    print("- Build automated inference workflows")

if __name__ == "__main__":
    asyncio.run(main())