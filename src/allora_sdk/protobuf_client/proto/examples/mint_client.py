"""
Example: Using the Mint gRPC client with betterproto
"""

import asyncio
from mint.v1beta1 import QueryServiceStub, QueryParamsRequest

async def get_mint_params():
    """Example: Query mint parameters"""
    async with QueryServiceStub("localhost:9090") as stub:
        try:
            response = await stub.params(QueryParamsRequest())
            print(f"Mint params: {response.params}")
            return response.params
        except Exception as e:
            print(f"Error querying mint params: {e}")
            return None

async def main():
    await get_mint_params()

if __name__ == "__main__":
    asyncio.run(main())
