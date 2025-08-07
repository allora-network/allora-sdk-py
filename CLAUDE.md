# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

The Allora Network Python SDK is a comprehensive async client library for interacting with the Allora blockchain network. It provides both HTTP API access and direct blockchain interaction capabilities including transaction submission and real-time event subscriptions.

### Core Architecture

- **Protobuf Client**: `src/allora_sdk/protobuf_client/client.py` contains the main `ProtobufClient` class for blockchain interaction
- **HTTP API Client**: `src/allora_sdk/api_client_v2.py` provides REST API access for queries and data retrieval  
- **Event Subscriptions**: WebSocket-based real-time event streaming with typed protobuf message support
- **Transaction Support**: Full transaction building and submission for worker payload inference submission
- **Chain Support**: Supports testnet, mainnet, and local development chains

### Key Components

#### Blockchain Interaction (`protobuf_client`)
- `ProtobufClient`: Main blockchain client with transaction, query, and event capabilities
- `AlloraTransactions`: Transaction builder supporting worker payload submissions and standard Cosmos operations
- `AlloraWebsocketSubscriber`: Real-time event subscription with both generic and typed protobuf callbacks
- `EventRegistry`: Auto-discovers protobuf Event classes for type-safe event handling
- `AlloraQueries`: Blockchain query interface (queries, balances, account info, etc.)

#### HTTP API (`api_client_v2`)  
- `AlloraAPIClient`: HTTP client for topics, inferences, and price predictions
- `AlloraTopic`: Model for network topics with metadata
- `AlloraInference`: Model for inference data with confidence intervals

#### Protobuf Integration
- Full Allora protobuf message support (emissions v1-v9)
- betterproto-based message serialization with cosmpy integration
- Type-safe event marshaling from JSON to protobuf instances

## Common Development Commands

### Testing
```bash
# Run all tests across Python versions
tox

# Run tests for specific Python version
tox -e 3.12

# Run tests directly with pytest
pytest tests/

# Run specific test file
pytest tests/test_api_client_unit.py

# Run integration tests (requires API access)
pytest tests/test_api_client_integration.py
```

### Linting and Type Checking
```bash
# Run linting (black formatter)
tox -e lint

# Run type checking
tox -e type

# Format code manually
black .

# Type check manually
mypy src tests
```

### Building and Installation
```bash
# Install in development mode
pip install -e .

# Install with dev dependencies
pip install -e .[dev]

# Build wheel
python -m build
```

## Testing Strategy

The project uses a dual testing approach:

1. **Unit Tests** (`test_api_client_unit.py`): Mock-based tests using custom `StarletteMockFetcher` that simulates API responses
2. **Integration Tests** (`test_api_client_integration.py`): Real API tests against testnet (requires network access)

The mock testing framework in `tests/mock_data.py` provides a `MockServer` class that can simulate API responses and pagination scenarios.

## Transaction API Usage

### Worker Payload Submission

The SDK supports submitting worker inferences to the Allora network via blockchain transactions:

```python
from allora_sdk.protobuf_client.client import ProtobufClient

# Initialize client with wallet
client = ProtobufClient.testnet(private_key="your_hex_private_key")
# or 
client = ProtobufClient.testnet(mnemonic="your mnemonic phrase")

# Submit inference for a topic
response = await client.transactions.submit_worker_payload(
    topic_id=13,
    inference_value="42.5",
    memo="My inference submission"
)

print(f"Transaction hash: {response.txhash}")
print(f"Success: {response.code == 0}")
```

### Advanced Usage

```python
# Submit with forecast elements
response = await client.transactions.submit_worker_payload(
    topic_id=13,
    inference_value="42.5",
    forecast_elements=[
        {"inferer": "allo1abc123...", "value": "43.0"},
        {"inferer": "allo1def456...", "value": "41.8"}
    ],
    extra_data=b"custom_metadata",
    proof="proof_string",
    gas_limit=400000
)

# Check transaction status
if response.code == 0:
    print(f"✅ Transaction successful: {response.txhash}")
else:
    print(f"❌ Transaction failed: {response.raw_log}")
```

### Event Listening + Transaction Flow

```python
from allora_sdk.protobuf_client.proto.emissions.v9 import EventScoresSet

# Listen for score events and submit new inferences
async def on_scores_set(events):
    print(f"Received {len(events)} score events")
    for event in events:
        print(f"Topic {event.topic_id} scores updated at block {event.block_height}")
        # Submit new inference for next epoch
        # await client.transactions.submit_worker_payload(...)

# Subscribe to typed events
await client.events.subscribe_new_block_events_typed(
    EventScoresSet,
    [EventAttributeCondition("topic_id", "CONTAINS", "13")],
    on_scores_set
)
```

## API Design Patterns

- All API methods are async and return typed Pydantic models
- Error handling raises `ValueError` for API-level errors and lets `aiohttp` exceptions bubble up for network issues
- Pagination is handled automatically in `get_all_topics()` method
- Default API key and base URL are provided but can be overridden via constructor or environment variables
- Signature formats are configurable via `SignatureFormat` enum (currently supports Ethereum Sepolia)