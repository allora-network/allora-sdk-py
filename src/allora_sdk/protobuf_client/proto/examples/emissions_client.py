"""
Example: Using the Emissions gRPC client with betterproto
"""

import asyncio
from emissions.v1 import QueryServiceStub, QueryParamsRequest

async def get_emissions_params():
    """Example: Query emissions parameters"""
    async with QueryServiceStub("localhost:9090") as stub:
        try:
            response = await stub.params(QueryParamsRequest())
            print(f"Emissions params: {response.params}")
            return response.params
        except Exception as e:
            print(f"Error querying emissions params: {e}")
            return None

async def main():
    await get_emissions_params()

if __name__ == "__main__":
    asyncio.run(main())
