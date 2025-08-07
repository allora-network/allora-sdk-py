# Allora Network Python Protobuf Library

This library provides Python bindings for Allora Network's protobuf definitions, generated using betterproto. It offers type-safe Python interfaces for interacting with Allora Network's gRPC services.

## Features

- **Type-safe Python bindings** generated from protobuf definitions using betterproto
- **Async/await support** for all gRPC operations
- **Modern Python dataclass-style messages** with betterproto
- **Comprehensive coverage** of Allora Network's modules
- **Easy-to-use client interfaces** for gRPC services

## Installation

### From PyPI (when published)

```bash
pip install allora-python-proto
```

### From source

```bash
git clone https://github.com/allora-network/allora-python-proto.git
cd allora-python-proto
pip install -e .
```

## Quick Start

### Basic Usage

```python
import asyncio
from mint.v1beta1 import QueryServiceStub, QueryParamsRequest

async def main():
    # Create a gRPC client
    async with QueryServiceStub("localhost:9090") as stub:
        # Query mint parameters
        response = await stub.params(QueryParamsRequest())
        print(f"Mint params: {response.params}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Advanced Usage

```python
import asyncio
from mint.v1beta1 import QueryServiceStub, QueryParamsRequest, QueryAnnualProvisionsRequest

async def query_mint_data():
    async with QueryServiceStub("localhost:9090") as stub:
        # Query parameters
        params_response = await stub.params(QueryParamsRequest())
        print(f"Parameters: {params_response.params}")
        
        # Query annual provisions
        provisions_response = await stub.annual_provisions(QueryAnnualProvisionsRequest())
        print(f"Annual provisions: {provisions_response.annual_provisions}")

async def main():
    await query_mint_data()

if __name__ == "__main__":
    asyncio.run(main())
```

## Available Modules

### Mint Module

The mint module provides access to Allora Network's mint-related gRPC services:

- **QueryServiceStub** - Query mint data and parameters
  - `params(request: QueryParamsRequest) -> QueryParamsResponse`
  - `annual_provisions(request: QueryAnnualProvisionsRequest) -> QueryAnnualProvisionsResponse`

- **MsgServiceStub** - Submit mint-related transactions
  - `update_params(request: MsgUpdateParams) -> MsgUpdateParamsResponse`

### Emissions Module

The emissions module provides access to Allora Network's emissions-related gRPC services:

- **QueryServiceStub** - Query emissions data and parameters
  - `params(request: QueryParamsRequest) -> QueryParamsResponse`
  - `topics(request: QueryTopicsRequest) -> QueryTopicsResponse`
  - `topics_by_name(request: QueryTopicsByNameRequest) -> QueryTopicsByNameResponse`

- **MsgServiceStub** - Submit emissions-related transactions
  - `insert_emissions_epochs(request: MsgInsertEmissionsEpochs) -> MsgInsertEmissionsEpochsResponse`

## Development

### Prerequisites

- Python 3.8+
- protoc compiler
- Docker (for containerized development)

### Setting up the development environment

```bash
# Install protoc compiler
# macOS: brew install protobuf
# Ubuntu: sudo apt-get install protobuf-compiler

# Install Python dependencies
pip install -e ".[dev]"
```

### Generating Code

To regenerate the protobuf code:

```bash
python generate_betterproto_complete.py
```

### Running Tests

```bash
pytest
```

### Code Formatting

```bash
# Format code
black .
isort .

# Type checking
mypy .

# Linting
flake8 .
```

### Building

```bash
python -m build
```

## API Reference

### Mint Module

#### QueryServiceStub

- `params(request: QueryParamsRequest) -> QueryParamsResponse`
- `annual_provisions(request: QueryAnnualProvisionsRequest) -> QueryAnnualProvisionsResponse`

#### MsgServiceStub

- `update_params(request: MsgUpdateParams) -> MsgUpdateParamsResponse`

### Emissions Module

#### QueryServiceStub

- `params(request: QueryParamsRequest) -> QueryParamsResponse`
- `topics(request: QueryTopicsRequest) -> QueryTopicsResponse`
- `topics_by_name(request: QueryTopicsByNameRequest) -> QueryTopicsByNameResponse`

#### MsgServiceStub

- `insert_emissions_epochs(request: MsgInsertEmissionsEpochs) -> MsgInsertEmissionsEpochsResponse`

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass
6. Submit a pull request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

For support and questions:

- Create an issue on GitHub
- Check the documentation
- Review the examples

## Changelog

### 0.1.0

- Initial release
- Support for mint and emissions modules
- Type-safe Python bindings using betterproto
- Async gRPC client support
- Modern dataclass-style messages
- Comprehensive build automation
- Development tools integration
