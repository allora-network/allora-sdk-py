#!/usr/bin/env python3
"""
Capture RPC REST responses from Allora testnet for use as test fixtures.

This script queries the Allora testnet LCD (REST) endpoints and saves the responses
as JSON fixtures in tests/fixtures/rpc_responses/.

Usage:
    python scripts/capture_rpc_responses.py [--modules auth,bank,tx] [--output-dir tests/fixtures/rpc_responses]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime

import aiohttp


TESTNET_URL = "https://allora-api.testnet.allora.network"

# Use a known testnet address with valid checksum
TEST_ADDRESS = "allo1lvdpcyf7jtdn45gradfmxzwqlapxa9zd3kr4pj"

# Known topic ID that should exist on testnet
TEST_TOPIC_ID = "1"

# Known block height for historical queries
TEST_BLOCK_HEIGHT = "1000"


class ResponseCapture:
    """Capture and save RPC REST responses from Allora testnet."""

    def __init__(self, base_url: str, output_dir: Path):
        self.base_url = base_url.rstrip("/")
        self.output_dir = output_dir
        self.session: Optional[aiohttp.ClientSession] = None
        self.captured = []

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def fetch(self, endpoint: str, name: str, params: Optional[Dict[str, Any]] = None,
                    headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Fetch an endpoint and save the response.

        Args:
            endpoint: The API endpoint path (e.g., "/cosmos/auth/v1beta1/params")
            name: The fixture file name (without .json extension)
            params: Optional query parameters
            headers: Optional request headers
        """
        url = f"{self.base_url}{endpoint}"

        try:
            print(f"Fetching {endpoint}...")
            async with self.session.get(url, params=params, headers=headers) as response:
                status = response.status
                try:
                    data = await response.json()
                except Exception:
                    # If JSON parsing fails, get text
                    text = await response.text()
                    data = {"error": "Failed to parse JSON", "text": text[:500]}

                result = {
                    "endpoint": endpoint,
                    "status": status,
                    "captured_at": datetime.utcnow().isoformat(),
                    "url": url,
                    "params": params,
                    "headers": headers,
                    "data": data
                }

                # Save to file
                output_file = self.output_dir / f"{name}.json"
                output_file = output_file.resolve()  # Resolve to absolute path
                output_file.parent.mkdir(parents=True, exist_ok=True)
                with open(output_file, "w") as f:
                    json.dump(result, f, indent=2)

                self.captured.append(name)
                print(f"  ✓ Saved to {output_file.relative_to(Path.cwd())}")

                return result

        except Exception as e:
            print(f"  ✗ Error: {e}")
            error_result = {
                "endpoint": endpoint,
                "status": 0,
                "captured_at": datetime.utcnow().isoformat(),
                "url": url,
                "error": str(e)
            }

            # Save error response too
            output_file = self.output_dir / f"{name}_error.json"
            output_file = output_file.resolve()  # Resolve to absolute path
            with open(output_file, "w") as f:
                json.dump(error_result, f, indent=2)

            return error_result

    async def capture_auth_endpoints(self):
        """Capture Auth module endpoints."""
        print("\n=== Capturing Auth Module ===")

        # First, get accounts list to find a valid address
        accounts_resp = await self.fetch(
            "/cosmos/auth/v1beta1/accounts",
            "auth_accounts",
            params={"pagination.limit": "10"}
        )

        # Try to extract a valid address from the response
        test_addr = TEST_ADDRESS
        if accounts_resp.get("status") == 200:
            data = accounts_resp.get("data", {})
            accounts = data.get("accounts", [])
            if accounts and len(accounts) > 0:
                # Try to get address from first account
                first_account = accounts[0]
                if "address" in first_account:
                    test_addr = first_account["address"]

        # Account by address
        await self.fetch(
            f"/cosmos/auth/v1beta1/accounts/{test_addr}",
            "auth_account"
        )

        # Account with block height header
        await self.fetch(
            f"/cosmos/auth/v1beta1/accounts/{test_addr}",
            "auth_account_at_height",
            headers={"x-cosmos-block-height": TEST_BLOCK_HEIGHT}
        )

        # Params
        await self.fetch(
            "/cosmos/auth/v1beta1/params",
            "auth_params"
        )

        # Account info
        await self.fetch(
            f"/cosmos/auth/v1beta1/account_info/{test_addr}",
            "auth_account_info"
        )

        # Module accounts
        await self.fetch(
            "/cosmos/auth/v1beta1/module_accounts",
            "auth_module_accounts"
        )

    async def capture_bank_endpoints(self):
        """Capture Bank module endpoints."""
        print("\n=== Capturing Bank Module ===")

        # Use the same test address
        test_addr = TEST_ADDRESS

        # All balances
        await self.fetch(
            f"/cosmos/bank/v1beta1/balances/{test_addr}",
            "bank_balances"
        )

        # Balance by denom
        await self.fetch(
            f"/cosmos/bank/v1beta1/balances/{test_addr}/by_denom",
            "bank_balance_by_denom",
            params={"denom": "uallo"}
        )

        # Spendable balances
        await self.fetch(
            f"/cosmos/bank/v1beta1/spendable_balances/{test_addr}",
            "bank_spendable_balances"
        )

        # Total supply
        await self.fetch(
            "/cosmos/bank/v1beta1/supply",
            "bank_supply"
        )

        # Supply by denom
        await self.fetch(
            "/cosmos/bank/v1beta1/supply/by_denom",
            "bank_supply_by_denom",
            params={"denom": "uallo"}
        )

        # Params
        await self.fetch(
            "/cosmos/bank/v1beta1/params",
            "bank_params"
        )

        # Denom metadata
        await self.fetch(
            "/cosmos/bank/v1beta1/denoms_metadata",
            "bank_denoms_metadata"
        )

    async def capture_tx_endpoints(self):
        """Capture TX module endpoints."""
        print("\n=== Capturing TX Module ===")

        # Get a recent transaction hash first
        # We'll query latest block and get txs from there
        latest_block = await self.fetch(
            "/cosmos/base/tendermint/v1beta1/blocks/latest",
            "tendermint_latest_block"
        )

        # Try to extract a tx hash if any txs exist
        tx_hash = None
        if latest_block.get("status") == 200:
            data = latest_block.get("data", {})
            block = data.get("block", {})
            block_data = block.get("data", {})
            txs = block_data.get("txs", [])
            if txs and len(txs) > 0:
                # txs are base64 encoded, but we need the hash
                # Let's try to get from a different endpoint
                pass

        # For now, capture the simulate endpoint structure (will be empty but shows format)
        # In real tests, we'll mock with proper data

    async def capture_emissions_endpoints(self):
        """Capture Emissions module endpoints (key endpoints only)."""
        print("\n=== Capturing Emissions Module ===")

        topic_id = TEST_TOPIC_ID
        test_addr = TEST_ADDRESS
        block_height = TEST_BLOCK_HEIGHT

        # Topics & Metadata
        await self.fetch(
            f"/emissions/v9/topics/{topic_id}",
            "emissions_topic"
        )

        await self.fetch(
            "/emissions/v9/active_topics",
            "emissions_active_topics",
            params={"pagination.limit": "10"}
        )

        await self.fetch(
            "/emissions/v9/params",
            "emissions_params"
        )

        # Inferences & Forecasts
        await self.fetch(
            f"/emissions/v9/network_inference/topic/{topic_id}",
            "emissions_network_inference"
        )

        await self.fetch(
            f"/emissions/v9/latest_network_inference_by_topic_id/{topic_id}",
            "emissions_latest_network_inference"
        )

        await self.fetch(
            f"/emissions/v9/inferences_at_block/{topic_id}/{block_height}",
            "emissions_inferences_at_block"
        )

        await self.fetch(
            f"/emissions/v9/forecasts_at_block/{topic_id}/{block_height}",
            "emissions_forecasts_at_block"
        )

        # Workers & Reputers
        await self.fetch(
            f"/emissions/v9/is_worker_registered/{topic_id}/{test_addr}",
            "emissions_is_worker_registered"
        )

        await self.fetch(
            f"/emissions/v9/is_reputer_registered/{topic_id}/{test_addr}",
            "emissions_is_reputer_registered"
        )

        await self.fetch(
            f"/emissions/v9/worker_latest_inference_by_topicId/{topic_id}",
            "emissions_worker_latest_inference",
            params={"worker_address": test_addr}
        )

        # Stakes & Rewards
        await self.fetch(
            f"/emissions/v9/stake_placement/{topic_id}/{test_addr}",
            "emissions_stake_placement"
        )

        await self.fetch(
            f"/emissions/v9/total_stake/{topic_id}",
            "emissions_total_stake"
        )

        await self.fetch(
            f"/emissions/v9/topic_reward_nonce/{topic_id}",
            "emissions_topic_reward_nonce"
        )

        # Loss Bundles
        await self.fetch(
            f"/emissions/v9/loss_bundles_at_block/{topic_id}/{block_height}",
            "emissions_loss_bundles_at_block"
        )

    async def capture_feemarket_endpoints(self):
        """Capture Feemarket module endpoints."""
        print("\n=== Capturing Feemarket Module ===")

        await self.fetch(
            "/feemarket/v1/gas_price",
            "feemarket_gas_price"
        )

        await self.fetch(
            "/feemarket/v1/params",
            "feemarket_params"
        )

        await self.fetch(
            "/feemarket/v1/state",
            "feemarket_state"
        )

    async def capture_mint_endpoints(self):
        """Capture Mint module endpoints."""
        print("\n=== Capturing Mint Module ===")

        await self.fetch(
            "/mint/v5/params",
            "mint_params"
        )

        await self.fetch(
            "/mint/v5/inflation",
            "mint_inflation"
        )

        await self.fetch(
            "/mint/v5/annual_provisions",
            "mint_annual_provisions"
        )

    async def capture_tendermint_endpoints(self):
        """Capture Tendermint module endpoints."""
        print("\n=== Capturing Tendermint Module ===")

        await self.fetch(
            "/cosmos/base/tendermint/v1beta1/node_info",
            "tendermint_node_info"
        )

        # Latest block already captured in TX section

        await self.fetch(
            f"/cosmos/base/tendermint/v1beta1/blocks/{TEST_BLOCK_HEIGHT}",
            "tendermint_block_by_height"
        )

    async def capture_all(self, modules: Optional[list[str]] = None):
        """Capture all endpoints for specified modules."""

        if modules is None:
            modules = ["auth", "bank", "tx", "emissions", "feemarket", "mint", "tendermint"]

        if "auth" in modules:
            await self.capture_auth_endpoints()

        if "bank" in modules:
            await self.capture_bank_endpoints()

        if "tx" in modules:
            await self.capture_tx_endpoints()

        if "emissions" in modules:
            await self.capture_emissions_endpoints()

        if "feemarket" in modules:
            await self.capture_feemarket_endpoints()

        if "mint" in modules:
            await self.capture_mint_endpoints()

        if "tendermint" in modules:
            await self.capture_tendermint_endpoints()

        print(f"\n✓ Captured {len(self.captured)} responses")
        print(f"  Saved to: {self.output_dir}")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Capture RPC REST responses from Allora testnet")
    parser.add_argument(
        "--modules",
        type=str,
        default="all",
        help="Comma-separated list of modules to capture (default: all)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/fixtures/rpc_responses"),
        help="Output directory for fixtures (default: tests/fixtures/rpc_responses)"
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=TESTNET_URL,
        help=f"Base URL for RPC endpoint (default: {TESTNET_URL})"
    )

    args = parser.parse_args()

    # Parse modules
    if args.modules == "all":
        modules = None
    else:
        modules = [m.strip() for m in args.modules.split(",")]

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Capturing responses from: {args.base_url}")
    print(f"Output directory: {args.output_dir}")

    async with ResponseCapture(args.base_url, args.output_dir) as capturer:
        await capturer.capture_all(modules)

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
