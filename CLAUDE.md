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


## Coding Style Guidelines

### Structure and Control Flow
- **Minimize indentation**: Prefer early returns over nested blocks. Avoid nested try/catch and nested if statements.
- **Guard clauses**: Use guard clauses at the beginning of functions to handle edge cases and exit early.
- **Single responsibility**: Each function should have one clear purpose.

```python
# Preferred - early returns, minimal nesting
def process_events(events: List[Dict[str, Any]]) -> List[ProcessedEvent]:
    if not events:
        return []
    
    filtered = [ e for e in events if e.get("type") == "target_type" ]
    if not filtered:
        return []
    
    return [ ProcessedEvent.from_dict(e) for e in filtered ]

# Avoid - nested blocks
def process_events_bad(events: List[Dict[str, Any]]) -> List[ProcessedEvent]:
    if events:
        filtered = []
        for e in events:
            if e.get("type") == "target_type":
                filtered.append(e)
        if filtered:
            results = []
            for e in filtered:
                results.append(ProcessedEvent.from_dict(e))
            return results
    return []
```

### Comprehensions and Data Processing
- **Use comprehensions liberally**: Even multiple comprehensions in succession for complex transformations.
- **Chain comprehensions**: It's acceptable to do multiple passes for clarity.

```python
# Preferred - clear, functional style, using same list variable name
events = websocket_data.get("events", [])
events = [ e for e in events if e.get("type") == target_type ]
events = [ marshal_event(e) for e in events ]
events = [ e for e in events if e is not None ]
```

### Type Safety and Documentation
- **Strong typing always**: Use type hints on all functions, variables, and class attributes.
- **Prefer structured types**: Use dataclasses, Pydantic models, or NamedTuple over dictionaries.
- **Convert external data**: Transform incoming dictionaries from external packages into typed objects.
- **Use enums**: For constants and fixed sets of values.
- **Leverage generics**: Use TypeVar and Generic for reusable, type-safe code.
- **Avoid Any/Unknown**: Only use when absolutely necessary for external library compatibility.

```python
from enum import Enum
from typing import Optional, List, Generic, TypeVar
from dataclasses import dataclass

class EventType(Enum):
    SCORES_SET = "scores_set"
    INFERENCE_RECEIVED = "inference_received"

@dataclass
class ProcessedEvent:
    event_type: EventType
    topic_id: int
    block_height: Optional[int]
    data: Dict[str, Any]
    
    @classmethod
    def from_websocket_data(cls, raw_data: Dict[str, Any]) -> Optional['ProcessedEvent']:
        """Convert raw WebSocket data to typed ProcessedEvent."""
        event_type_str = raw_data.get("type")
        if not event_type_str:
            return None
            
        try:
            event_type = EventType(event_type_str)
        except ValueError:
            return None
            
        return cls(
            event_type=event_type,
            topic_id=int(raw_data.get("topic_id", 0)),
            block_height=raw_data.get("block_height"),
            data=raw_data
        )
```

### Documentation Standards
- **Document all public functions**: Provide comprehensive docstrings with arguments and return values for public functions.
- **Document all classes**: Include purpose, usage patterns, and key methods.
- **One-line docs for private functions**: At minimum, explain what the function does.
- **Use standard formats**: Follow Google or NumPy docstring conventions.
- **Don't comment excessively within function bodies**: If your code is elegant, it doesn't need explanations of what's happening every few statements.  Lean on writing elegant, expressive code.  Name your variables and functions well.  Break up logic into coherent, understandable units (which doesn't mean you should break every big piece of code into tons of functions!  Sometimes it's a subtler form of organization that wins the day).
- **If a function or class might need more extensive documentation**, perhaps in the form of examples or further discussion, add a `docs/` folder and start documenting things there in greater depth.
- **The README** should mainly just contain a description of the package, a quickstart example, and a link to the `docs/` folder if you created one (keep in mind that this README will be rendered on Github).

Example of public function documentation:

```python
def subscribe_new_block_events(
    self, 
    event_name: str,
    conditions: List[EventAttributeCondition], 
    callback: Callable[[Dict[str, Any], Optional[int]], None],
) -> str:
    """
    Subscribe to specific blockchain events with filtering.
    
    This method creates a WebSocket subscription that filters events by name
    and attributes, calling the provided callback for each matching event.
    
    Args:
        event_name: The specific event type (e.g., "emissions.v9.EventScoresSet")
        conditions: List of attribute filters to apply
        callback: Function called for each event (event_data, block_height)
        
    Returns:
        Subscription ID for managing the subscription
    """

def _parse_websocket_message(self, raw_message: str) -> Optional[ParsedMessage]:
    """Parse incoming WebSocket message into structured data."""
```

### Development Practices
- **Use your Pyright tool extensively**: Use your pyright tool call frequently to catch type errors and understand third-party packages.  Your memorized information could be out of date!
- **4-space indentation**: Always use 4 spaces, never tabs.
- **Avoid inheritance**: Prefer composition unless required by external libraries.  Write code that's more like Go or Zig and less like Java or C++.  Python has its own style, but its flexibility gives us the option to bend that style in one direction or another.
- **Minimal logging**: Log errors when appropriate, but avoid verbose success/debug logs. Let users add debugging when needed.
- **Prefer simplicity**: Choose Go/Zig-style simplicity over Java/C++ complexity. Favor explicit over implicit.

### Error Handling
- **Explicit error handling**: Return None or raise specific exceptions rather than broad try/catch blocks.
- **Domain-specific exceptions**: Create custom exception types for different error categories.
- **Early validation**: Validate inputs at function entry points.

```python
class AlloraClientError(Exception):
    """Base exception for Allora client errors."""
    pass

class SubscriptionError(AlloraClientError):
    """Raised when event subscription operations fail."""
    pass

def create_subscription(self, event_filter: EventFilter) -> str:
    """Create new event subscription, returning subscription ID."""
    if not self.websocket_connected:
        raise SubscriptionError("WebSocket not connected")
        
    if not event_filter.conditions:
        raise SubscriptionError("Event filter cannot be empty")
        
    subscription_id = self._generate_subscription_id()
    # ... rest of implementation
    return subscription_id
```