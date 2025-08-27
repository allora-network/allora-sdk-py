"""
Allora Event Subscription System

This module provides WebSocket-based event subscription functionality
for monitoring Allora blockchain events in real-time.
"""

import asyncio
import json
import logging
import importlib
import inspect
from typing import AsyncIterable, Awaitable, Dict, Iterable, List, Callable, Any, Literal, Optional, Union, Type, TypeVar, Protocol, runtime_checkable
import websockets
import traceback
import betterproto2
from pydantic import BaseModel

logger = logging.getLogger("allora_sdk")


@runtime_checkable
class WebSocketLike(Protocol):
    """Protocol used to mock real websocket connection in testing."""

    @property
    def close_code(self) -> int | None: ...

    async def send(
        self,
        message: websockets.Data | Iterable[websockets.Data] | AsyncIterable[websockets.Data],
        text: bool | None = None,
    ): ...
    async def recv(self, decode: bool | None = None) -> websockets.Data: ...
    async def ping(self, data: websockets.Data | None = None) -> Awaitable[float]: ...
    async def close(self) -> Any: ...

# Abstracts the concrete type of a connection function, used mainly for testing.
ConnectFn = Callable[[str], Awaitable[WebSocketLike]]

async def default_websocket_connect(url: str) -> WebSocketLike:
    return await websockets.connect(url, ping_interval=20, ping_timeout=10)



T = TypeVar('T', bound=betterproto2.Message)

class NewBlockEventsData(BaseModel):
    height: str
    events: List[Any]  # Could be more specific based on actual event structure

class NewBlockEventsDataFrame(BaseModel):
    type: Literal["tendermint/event/NewBlockEvents"]
    value: NewBlockEventsData

# Placeholder for future query result types
class GenericQueryResultDataFrame(BaseModel):
    type: str
    value: dict

class JSONRPCQueryResult(BaseModel):
    query: str
    data: Union[NewBlockEventsDataFrame, GenericQueryResultDataFrame]
    
    def __init__(self, **data):
        # Custom parsing logic for discriminated union
        if 'data' in data and isinstance(data['data'], dict):
            data_type = data['data'].get('type')
            if data_type == "tendermint/event/NewBlockEvents":
                data['data'] = NewBlockEventsDataFrame(**data['data'])
            else:
                data['data'] = GenericQueryResultDataFrame(**data['data'])
        super().__init__(**data)

class JSONRPCResponse(BaseModel):
    jsonrpc: str
    id: str
    result: Optional[Union[JSONRPCQueryResult, dict]] = None

class EventAttributeCondition:
    """Represents a condition for filtering blockchain event attributes."""
    
    def __init__(self, attribute_name: str, operator: str, value: str):
        """
        Create an attribute condition for Tendermint query filtering.
        
        Args:
            attribute_name: The attribute key to filter on (e.g., "topic_id", "actor_type")
            operator: The comparison operator ("=", "<", "<=", ">", ">=", "CONTAINS", "EXISTS")
            value: The value to compare against (will be single-quoted in the query)
        """
        valid_operators = {"=", "<", "<=", ">", ">=", "CONTAINS", "EXISTS"}
        if operator not in valid_operators:
            raise ValueError(f"Invalid operator '{operator}'. Must be one of: {valid_operators}")
        
        self.attribute_name = attribute_name
        self.operator = operator
        self.value = value
    
    def to_query_condition(self) -> str:
        """Convert this condition to a Tendermint query string fragment."""
        if self.operator == "EXISTS":
            return f"{self.attribute_name} EXISTS"
        else:
            return f"{self.attribute_name} {self.operator} '{self.value}'"
    
    def __repr__(self):
        return f"EventAttributeCondition({self.attribute_name} {self.operator} {self.value})"

class EventRegistry:
    """Registry for mapping event type strings to protobuf Event classes."""
    
    _instance = None
    _event_map: Dict[str, Type[betterproto2.Message]] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._event_map:
            self._discover_event_classes()
    
    def _discover_event_classes(self) -> None:
        """Auto-discover Event classes from emissions protobuf modules."""
        versions: List[str] = ['v1', 'v2', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8', 'v9']
        
        for version in versions:
            try:
                module_name = f"allora_sdk.protos.emissions.{version}"
                module = importlib.import_module(module_name)
                
                event_classes = [
                    (name, obj) for name, obj in inspect.getmembers(module)
                    if (inspect.isclass(obj) and 
                        name.startswith('Event') and 
                        hasattr(obj, '__annotations__') and
                        issubclass(obj, betterproto2.Message))
                ]
                if len(event_classes) == 0:
                    logger.debug(f"no event classes in {module_name}")
                
                for name, obj in event_classes:
                    event_type = f"emissions.{version}.{name}"
                    self._event_map[event_type] = obj
                        
            except ImportError as err:
                print(f"ImportError: {err}")
                continue
    
    def get_event_class(self, event_type: str) -> Optional[Type[betterproto2.Message]]:
        """Get the protobuf class for an event type string."""
        return self._event_map.get(event_type)
    
    def list_event_types(self) -> List[str]:
        """List all registered event types."""
        return list(self._event_map.keys())
    
    def is_registered(self, event_type: str) -> bool:
        """Check if an event type is registered."""
        return event_type in self._event_map

class ParseError(Exception):
    pass


class EventMarshaler:
    """Marshals JSON event attributes to protobuf Event instances with schema-driven parsing."""
    
    def __init__(self, registry: EventRegistry, *, strict: bool = False):
        self.registry = registry
        self.strict = strict
        self._parser_cache: Dict[Type[betterproto2.Message], Dict[str, Callable[[Any], Any]]] = {}
    
    def marshal_event(self, event_json: Dict[str, Any]) -> Optional[betterproto2.Message]:
        """
        Convert a JSON event to a protobuf Event instance.
        
        Args:
            event_json: JSON event with 'type' and 'attributes' fields
            
        Returns:
            Protobuf event instance or None if type not registered
        """
        event_type = event_json.get('type')
        
        if not event_type:
            logger.warning("❌ Event JSON missing 'type' field")
            return None
        
        event_class = self.registry.get_event_class(event_type)
        if not event_class:
            logger.warning(f"❌ No protobuf class registered for event type: {event_type}")
            return None
        
        attributes = event_json.get('attributes', [])
        
        # Build parser table for the target event class
        resolved_annotations = self._get_resolved_annotations(event_class)
        field_values = self._parse_attributes(attributes, event_class, resolved_annotations)
        logger.debug(f"🔧 Parsed field values: {field_values}")
        
        try:
            # Create protobuf instance with parsed field values
            instance = event_class(**field_values)
            return instance
        except Exception as e:
            logger.error(f"❌ Failed to create {event_class.__name__} instance: {e}")
            logger.error(f"   Field values: {field_values}")
            return None
    
    def _parse_attributes(self, attributes: List[Dict[str, Any]], event_class: Type[betterproto2.Message], field_annotations: Dict[str, Any]) -> Dict[str, Any]:
        """Parse JSON attributes array into protobuf field values using a per-class parser table."""
        parsers = self._get_parsers_for_class(event_class, field_annotations)
        field_values: Dict[str, Any] = {}
        for attr in attributes:
            key = attr.get('key')
            raw_value = attr.get('value')
            if not key or raw_value is None:
                continue
            parser = parsers.get(key)
            if parser is None:
                if self.strict:
                    raise ParseError(f"Unknown field '{key}' for {event_class.__name__}")
                else:
                    logger.debug(f"Ignoring unknown field '{key}' for {event_class.__name__}")
                    continue
            try:
                field_values[key] = parser(raw_value)
            except Exception as e:
                message = f"Failed to parse field '{key}' as {type(parser).__name__}: {e}"
                if self.strict:
                    raise ParseError(message)
                else:
                    logger.warning(message)
                    # Leave original value if lenient
                    field_values[key] = raw_value
        return field_values

    def _get_resolved_annotations(self, message_cls: Type[betterproto2.Message]) -> Dict[str, Any]:
        """Resolve forward-referenced type annotations to actual classes."""
        try:
            import typing as _typing
            import importlib as _importlib
            module = _importlib.import_module(message_cls.__module__)
            return _typing.get_type_hints(message_cls, globalns=vars(module))
        except Exception:
            return getattr(message_cls, '__annotations__', {})
    
    def _parse_attribute_value(self, field_name: str, json_value: Any, field_annotations: Dict[str, Any]) -> Any:
        """Parse a single attribute using the parser derived from annotations."""
        expected = field_annotations.get(field_name)
        parser = self._build_parser(expected)
        try:
            return parser(json_value)
        except Exception as e:
            if self.strict:
                raise
            logger.warning(f"Failed to parse attribute {field_name}='{json_value}': {e}")
            return json_value

    def _instantiate_message(self, message_cls: Type[betterproto2.Message], data: Dict[str, Any]) -> betterproto2.Message:
        """Instantiate a betterproto message from a plain dict, coercing field types."""
        try:
            annotations: Dict[str, Any] = getattr(message_cls, '__annotations__', {})
            coerced: Dict[str, Any] = {}
            for key, value in data.items():
                expected_type = annotations.get(key)
                # Coerce simple scalars
                if expected_type is int and isinstance(value, str) and (value.isdigit() or (value.startswith('-') and value[1:].isdigit())):
                    coerced[key] = int(value)
                elif expected_type is float and isinstance(value, str) and self._is_float(value):
                    coerced[key] = float(value)
                else:
                    # Nested message handling
                    import inspect as _inspect
                    if _inspect.isclass(expected_type) and issubclass(expected_type, betterproto2.Message) and isinstance(value, dict):
                        coerced[key] = self._instantiate_message(expected_type, value)
                    else:
                        coerced[key] = value
            return message_cls(**coerced)
        except Exception as e:
            logger.warning(f"Failed to instantiate message {message_cls} from {data}: {e}")
            # Fallback to direct construction; may raise later if incompatible
            return message_cls(**data)
    
    def _is_float(self, value: str) -> bool:
        """Check if string represents a float."""
        try:
            float(value)
            return '.' in value or 'e' in value.lower()
        except ValueError:
            return False

    def _strip_quotes(self, s: str) -> str:
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            return s[1:-1]
        return s

    def _get_parsers_for_class(self, message_cls: Type[betterproto2.Message], annotations: Dict[str, Any]) -> Dict[str, Callable[[Any], Any]]:
        if message_cls in self._parser_cache:
            return self._parser_cache[message_cls]
        parsers: Dict[str, Callable[[Any], Any]] = {}
        for field_name, expected_type in annotations.items():
            parsers[field_name] = self._build_parser(expected_type)
        self._parser_cache[message_cls] = parsers
        return parsers

    def _build_parser(self, expected_type: Any) -> Callable[[Any], Any]:
        import typing as _typing
        import inspect as _inspect

        origin = _typing.get_origin(expected_type)
        args = _typing.get_args(expected_type)

        # Handle List[T]
        if origin in (list, List) and args:
            elem_parser = self._build_parser(args[0])

            def parse_list(v: Any) -> List[Any]:
                if isinstance(v, str):
                    s = self._strip_quotes(v)
                    if s.startswith('[') and s.endswith(']'):
                        parsed = json.loads(s)
                    else:
                        if self.strict:
                            raise ParseError(f"Expected JSON array string, got '{v}'")
                        parsed = [s]
                elif isinstance(v, list):
                    parsed = v
                else:
                    if self.strict:
                        raise ParseError(f"Expected list for {expected_type}, got {type(v)}")
                    parsed = [v]
                return [elem_parser(elem) for elem in parsed]

            return parse_list

        # Handle nested message
        if _inspect.isclass(expected_type) and issubclass(expected_type, betterproto2.Message):

            def parse_message(v: Any) -> betterproto2.Message:
                if isinstance(v, dict):
                    return self._instantiate_message(expected_type, v)
                if isinstance(v, str):
                    s = self._strip_quotes(v)
                    if (s.startswith('{') and s.endswith('}')) or (s.startswith('[') and s.endswith(']')):
                        parsed = json.loads(s)
                        if isinstance(parsed, dict):
                            return self._instantiate_message(expected_type, parsed)
                raise ParseError(f"Cannot parse value for {expected_type}: {v}")

            return parse_message

        # Handle scalars
        if expected_type is int:

            def parse_int(v: Any) -> int:
                if isinstance(v, int):
                    return v
                if isinstance(v, str):
                    s = self._strip_quotes(v)
                    if s.isdigit() or (s.startswith('-') and s[1:].isdigit()):
                        return int(s)
                raise ParseError(f"Invalid int: {v}")

            return parse_int

        if expected_type is float:

            def parse_float(v: Any) -> float:
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    s = self._strip_quotes(v)
                    if self._is_float(s) or s.isdigit() or (s.startswith('-') and s[1:].isdigit()):
                        return float(s)
                raise ParseError(f"Invalid float: {v}")

            return parse_float

        if expected_type is bool:

            def parse_bool(v: Any) -> bool:
                if isinstance(v, bool):
                    return v
                if isinstance(v, str):
                    if v == 'true':
                        return True
                    if v == 'false':
                        return False
                raise ParseError(f"Invalid bool: {v}")

            return parse_bool

        if expected_type is str:

            def parse_str(v: Any) -> str:
                if isinstance(v, str):
                    return self._strip_quotes(v)
                return str(v)

            return parse_str

        # Fallback: identity
        return lambda v: v

class EventFilter:
    """Event filter for subscription queries."""
    
    def __init__(self):
        self.conditions: List[str] = []
    
    def event_type(self, event_type: str) -> 'EventFilter':
        """Filter by event type (e.g., 'NewBlock', 'Tx')."""
        self.conditions.append(f"tm.event='{event_type}'")
        return self
    
    def message_action(self, action: str) -> 'EventFilter':
        """Filter by message action."""
        self.conditions.append(f"message.action='{action}'")
        return self
    
    def message_module(self, module: str) -> 'EventFilter':
        """Filter by message module."""
        self.conditions.append(f"message.module='{module}'")
        return self
    
    def attribute(self, key: str, value: Union[str, int, float]) -> 'EventFilter':
        """Filter by custom attribute."""
        if isinstance(value, str):
            self.conditions.append(f"{key}='{value}'")
        else:
            self.conditions.append(f"{key}={value}")
        return self

    def custom(self, query: str) -> 'EventFilter':
        self.conditions.append(query)
        return self
    
    def sender(self, address: str) -> 'EventFilter':
        """Filter by sender address."""
        self.conditions.append(f"message.sender='{address}'")
        return self
    
    def to_query(self) -> str:
        """Convert filter to Tendermint query string."""
        if not self.conditions:
            return "tm.event='NewBlock'"
        return " AND ".join(self.conditions)
    
    @staticmethod
    def new_blocks() -> 'EventFilter':
        """Filter for new block events."""
        return EventFilter().event_type('NewBlock')
    
    @staticmethod
    def transactions() -> 'EventFilter':
        """Filter for transaction events."""
        return EventFilter().event_type('Tx')
    


GenericSyncCallbackFn  = Callable[[Dict[str, Any], int], None]
GenericAsyncCallbackFn = Callable[[Dict[str, Any], int], Awaitable[None]]
GenericCallbackFn      = Union[GenericSyncCallbackFn, GenericAsyncCallbackFn]

TypedSyncCallbackFn  = Callable[[T, int], None]
TypedAsyncCallbackFn = Callable[[T, int], Awaitable[None]]
TypedCallbackFn      = Union[TypedSyncCallbackFn, TypedAsyncCallbackFn]


class AlloraWebsocketSubscriber:
    """
    WebSocket-based event subscriber for Allora blockchain events.
    
    Provides real-time event streaming with automatic reconnection,
    filtering, and callback management.
    """
    
    def __init__(self, url: str, connect_fn: ConnectFn = default_websocket_connect):
        """Initialize event subscriber with Allora client."""
        self.url = url
        self.connect_fn = connect_fn
        self.websocket: Optional['WebSocketLike'] = None
        self.subscriptions: Dict[str, Dict[str, Any]] = {}
        self.callbacks: Dict[str, List[Callable]] = {}
        self.running = False
        self.reconnect_delay = 5.0  # seconds
        self.max_reconnect_attempts = 10
        self._subscription_id_counter = 0
        
        # Initialize event registry and marshaler for typed subscriptions
        self.event_registry = EventRegistry()
        self.event_marshaler = EventMarshaler(self.event_registry)
        
    async def start(self):
        if self.running:
            logger.warning("Event subscriber already running")
            return
        
        self.running = True

        self._event_task = asyncio.create_task(self._event_loop())
        await self._connect()


    async def _ensure_started(self):
        if not self.running:
            await self.start()
    
    async def stop(self):
        self.running = False
        
        if hasattr(self, '_event_task') and self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
        
        for subscription_id in list(self.subscriptions.keys()):
            await self._unsubscribe(subscription_id)
        
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        

    async def subscribe(
        self,
        event_filter: EventFilter,
        callback: GenericCallbackFn,
        subscription_id: Optional[str] = None
    ) -> str:
        """
        Subscribe to events matching the filter.
        
        Args:
            event_filter: Filter defining which events to receive
            callback: Function to call for each matching event (event, block_height)
            subscription_id: Optional custom subscription ID
            
        Returns:
            Subscription ID for managing the subscription
        """
        # Auto-start the event subscription service if not already running
        await self._ensure_started()
        
        if not subscription_id:
            self._subscription_id_counter += 1
            subscription_id = f"sub_{self._subscription_id_counter}"
        
        query = event_filter.to_query()
        
        # Store subscription info
        self.subscriptions[subscription_id] = {
            "query": query,
            "filter": event_filter,
            "active": False,
            "subscription_type": "tendermint_query"
        }
        
        # Store callback
        if subscription_id not in self.callbacks:
            self.callbacks[subscription_id] = []
        self.callbacks[subscription_id].append(callback)
        
        # Send subscription if connected
        if self.websocket and not self.websocket.close_code:
            await self._send_subscription(subscription_id, query)
        
        logger.debug(f"Subscribed to events: {query} (ID: {subscription_id})")
        return subscription_id
    
    async def unsubscribe(self, subscription_id: str):
        """Unsubscribe from events."""
        if subscription_id not in self.subscriptions:
            logger.debug(f"Subscription {subscription_id} not found")
            return
        
        await self._unsubscribe(subscription_id)
        
        # Remove from local storage
        self.subscriptions.pop(subscription_id, None)
        self.callbacks.pop(subscription_id, None)
        
        logger.debug(f"Unsubscribed from {subscription_id}")
    
    async def _connect(self):
        """Establish WebSocket connection."""
        attempts = 0
        while attempts < self.max_reconnect_attempts and self.running:
            try:
                logger.debug(f"Connecting to {self.url}")
                self.websocket = await self.connect_fn(self.url)
                logger.debug("WebSocket connected")
                
                # Resubscribe to all active subscriptions
                for subscription_id, info in self.subscriptions.items():
                    if not info["active"]:
                        await self._send_subscription(subscription_id, info["query"])
                
                return
                
            except Exception as e:
                attempts += 1
                logger.error(f"Connection attempt {attempts} failed: {e}")
                if attempts < self.max_reconnect_attempts:
                    await asyncio.sleep(self.reconnect_delay)
                else:
                    logger.error("Max reconnection attempts reached")
                    raise
    
    async def _send_subscription(self, subscription_id: str, query: str):
        """Send subscription request."""
        if not self.websocket or self.websocket.close_code:
            return
        
        request = {
            "jsonrpc": "2.0",
            "method": "subscribe", 
            "id": subscription_id,
            "params": {"query": query}
        }
        
        try:
            await self.websocket.send(json.dumps(request))
        except Exception as e:
            logger.error(f"❌ Failed to send subscription {subscription_id}: {e}")
    
    async def _unsubscribe(self, subscription_id: str):
        """Send unsubscribe request."""
        if not self.websocket or self.websocket.close_code:
            return
        
        request = {
            "jsonrpc": "2.0",
            "method": "unsubscribe",
            "id": subscription_id,
            "params": {"query": self.subscriptions[subscription_id]["query"]}
        }
        
        try:
            await self.websocket.send(json.dumps(request))
            self.subscriptions[subscription_id]["active"] = False
            pass  # Unsubscribe request sent successfully
        except Exception as e:
            logger.error(f"Failed to unsubscribe {subscription_id}: {e}")
    
    async def _event_loop(self):
        """Main event processing loop."""
        while self.running:
            try:
                if not self.websocket or self.websocket.close_code:
                    logger.info("Reconnecting...")
                    await self._connect()
                    continue
                
                try:
                    message = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=30.0
                    )
                    await self._handle_message(str(message))
                    
                except asyncio.TimeoutError:
                    # Send ping to keep connection alive
                    if not self.websocket.close_code:
                        await self.websocket.ping()
                    continue
                    
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed")
                self.websocket = None
                if self.running:
                    await asyncio.sleep(self.reconnect_delay)
                    
            except Exception as e:
                logger.error(f"Event loop error: {e}")
                import traceback
                logger.error(f"Event loop traceback: {traceback.format_exc()}")
                await asyncio.sleep(1.0)
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            message_id = data.get("id")
            
            # Handle subscription confirmations
            if data.get("result", {}).get("data") is None and "id" in data:
                subscription_id = data["id"]
                if subscription_id in self.subscriptions:
                    self.subscriptions[subscription_id]["active"] = True
                return
            
            # Try to parse as structured JSONRPCResponse
            events = None
            block_height = None
            message_id = data.get("id")
            
            try:
                msg = JSONRPCResponse.model_validate(data)
                if (isinstance(msg.result, JSONRPCQueryResult) and 
                    isinstance(msg.result.data, NewBlockEventsDataFrame)):
                    events = msg.result.data.value.events
                    block_height = int(msg.result.data.value.height) if msg.result.data.value.height else None
                else:
                    events = None
                    block_height = None
                    logger.error(f"⚠️ Structured parsing failed: wrong result type")
            except Exception as e:
                # Fall back to manual extraction
                result_data = data.get("result", {}).get("data", {}).get("value", {})
                events = result_data.get("events")
                height_str = result_data.get("height")
                try:
                    block_height = int(height_str) if height_str else None
                except (ValueError, TypeError):
                    block_height = None
            
            # Dispatch events if found
            if events is not None:
                await self._dispatch_events(events, message_id, block_height)
            
            # Handle errors
            if "error" in data:
                logger.error(f"Subscription error: {data['error']}")
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")
            logger.error(f"Message content (first 500 chars): {message[:500]}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            import traceback
            logger.error(f"Message handling traceback: {traceback.format_exc()}")
            logger.error(f"Message content (first 500 chars): {message[:500]}")
    
    async def _dispatch_events(
        self,
        event_data: List[Dict[str, Any]],
        target_subscription_id: Optional[str] = None,
        block_height: Optional[int] = None,
    ) -> None:
        """Dispatch events to registered callbacks based on subscription matching."""
        if not target_subscription_id:
            return
        
        if target_subscription_id not in self.subscriptions:
            return
            
        target_query: str = self.subscriptions[target_subscription_id]["query"]
        
        matching_subscriptions: List[str] = [
            sub_id for sub_id, sub_info in self.subscriptions.items()
            if (sub_info.get("query") == target_query and 
                sub_info.get("active", False) and
                sub_id in self.callbacks)
        ]
        
        if not matching_subscriptions:
            return
        
        for subscription_id in matching_subscriptions:
            await self._dispatch_to_subscription(subscription_id, event_data, block_height)
    
    async def _dispatch_to_subscription(
        self,
        subscription_id: str,
        event_data: List[Dict[str, Any]],
        block_height: Optional[int] = None,
    ) -> None:
        """Dispatch events to a specific subscription with filtering and type marshaling."""
        if not event_data:
            return
        
        subscription_info = self.subscriptions.get(subscription_id)
        if not subscription_info:
            return
            
        callbacks = self.callbacks.get(subscription_id)
        if not callbacks:
            return
            
        subscription_type = subscription_info.get("subscription_type")
        if not subscription_type:
            return
        
        if subscription_type == "NewBlockEvents":
            await self._handle_block_events(subscription_id, subscription_info, callbacks, event_data, block_height)
        elif subscription_type == "TypedNewBlockEvents":
            await self._handle_typed_block_events(subscription_id, subscription_info, callbacks, event_data, block_height)
        else:
            await self._handle_generic_events(subscription_id, callbacks, event_data, block_height)
    
    async def _handle_block_events(
        self,
        subscription_id: str,
        subscription_info: Dict[str, Any],
        callbacks: List[Callable],
        event_data: List[Dict[str, Any]],
        block_height: Optional[int],
    ) -> None:
        """Handle NewBlockEvents subscription type."""
        event_name = subscription_info.get("event_name")
        if not event_name:
            return
            
        events = [ e for e in event_data if e.get("type") == event_name ]
        if not events:
            return
            
        await self._execute_callbacks(callbacks, events, block_height, subscription_id)
    
    async def _handle_typed_block_events(
        self,
        subscription_id: str,
        subscription_info: Dict[str, Any],
        callbacks: List[Callable],
        event_data: List[Dict[str, Any]],
        block_height: Optional[int],
    ) -> None:
        """Handle TypedNewBlockEvents subscription type with protobuf marshaling."""
        event_name = subscription_info.get("event_name")
        event_class = subscription_info.get("event_class")
        if not (event_name and event_class):
            return
        
        events = [ e for e in event_data if e.get("type") == event_name ]
        events = [ self.event_marshaler.marshal_event(e) for e in events ]
        events = [ e for e in events if e is not None ]
        
        if not events:
            return
            
        await self._execute_callbacks(callbacks, events, block_height, subscription_id)
    
    async def _handle_generic_events(
        self,
        subscription_id: str,
        callbacks: List[Callable],
        event_data: List[Dict[str, Any]],
        block_height: Optional[int],
    ) -> None:
        """Handle generic tendermint query subscriptions."""
        await self._execute_callbacks(callbacks, event_data, block_height, subscription_id)
    
    async def _execute_callbacks(
        self,
        callbacks: List[Callable],
        events: List[Any],
        block_height: Optional[int],
        subscription_id: str,
    ) -> None:
        """Execute callbacks for each event, handling errors gracefully."""
        for callback in callbacks:
            for event in events:
                try:
                    # Check if callback is async
                    if asyncio.iscoroutinefunction(callback):
                        await callback(event, block_height)
                    else:
                        # Run sync callback in executor
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, callback, event, block_height)
                except Exception as e:
                    logger.error(f"Callback error for {subscription_id}: {e}")
                    logger.error(f"Callback traceback: {traceback.format_exc()}")
    
    # Convenience methods for common subscriptions
    
    async def subscribe_to_new_blocks(self, callback: GenericCallbackFn) -> str:
        """Subscribe to new block events."""
        return await self.subscribe(EventFilter.new_blocks(), callback)
    
    async def subscribe_to_transactions(self, callback: GenericCallbackFn) -> str:
        """Subscribe to transaction events."""
        return await self.subscribe(EventFilter.transactions(), callback)
    
    async def subscribe_to_address_activity(self, address: str, callback: GenericCallbackFn) -> str:
        """Subscribe to activity for a specific address."""
        event_filter = EventFilter.transactions().sender(address)
        return await self.subscribe(event_filter, callback)
    
    async def subscribe_new_block_events(
        self,
        event_name: str,
        event_attribute_conditions: List[EventAttributeCondition],
        callback: GenericCallbackFn,
        subscription_id: Optional[str] = None,
    ) -> str:
        """
        Subscribe to specific events within NewBlockEvents.
        
        Args:
            event_name: The specific event type to filter for (e.g., "emissions.v9.EventEMAScoresSet")
            event_attribute_conditions: List of attribute conditions to apply
            callback: Function to call for each matching event (event, block_height)
            subscription_id: Optional custom subscription ID
            
        Returns:
            Subscription ID for managing the subscription
        """
        # Auto-start the event subscription service if not already running
        await self._ensure_started()
        
        if not subscription_id:
            self._subscription_id_counter += 1
            subscription_id = f"block_events_{self._subscription_id_counter}"
        
        # Construct EventFilter with NewBlockEvents and attribute conditions
        event_filter = EventFilter().event_type('NewBlockEvents')
        for condition in event_attribute_conditions:
            event_filter.custom(event_name + "." + condition.to_query_condition())
        
        query = event_filter.to_query()
        
        # Store subscription info
        self.subscriptions[subscription_id] = {
            "query": query,
            "filter": event_filter,
            "event_name": event_name,
            "event_attribute_conditions": event_attribute_conditions,
            "active": False,
            "subscription_type": "NewBlockEvents"
        }
        
        # Store callback
        if subscription_id not in self.callbacks:
            self.callbacks[subscription_id] = []
        self.callbacks[subscription_id].append(callback)
        
        # Send subscription if connected
        if self.websocket and not self.websocket.close_code:
            await self._send_subscription(subscription_id, query)
        
        return subscription_id
    
    async def subscribe_new_block_events_typed(
        self,
        event_class: Type[T],
        event_attribute_conditions: List[EventAttributeCondition],
        callback: TypedCallbackFn,
        subscription_id: Optional[str] = None,
    ) -> str:
        """
        Subscribe to specific events within NewBlockEvents with typed protobuf callbacks.
        
        Args:
            event_class: The protobuf Event class to subscribe to (e.g., EventScoresSet)
            event_attribute_conditions: List of attribute conditions to apply
            callback: Function to call for each typed protobuf event (event, block_height)
            subscription_id: Optional custom subscription ID
            
        Returns:
            Subscription ID for managing the subscription
        """
        # Auto-start the event subscription service if not already running
        await self._ensure_started()
        
        if not subscription_id:
            self._subscription_id_counter += 1
            subscription_id = f"typed_block_events_{self._subscription_id_counter}"
        
        # Extract event name from class (e.g., EventScoresSet -> emissions.v9.EventScoresSet)
        event_name = self._get_event_type_from_class(event_class)
        if not event_name:
            logger.error(f"❌ Could not determine event type for class {event_class.__name__}")
            raise ValueError(f"Could not determine event type for class {event_class.__name__}")
        
        # Construct EventFilter with NewBlockEvents and attribute conditions
        event_filter = EventFilter().event_type('NewBlockEvents')
        for condition in event_attribute_conditions:
            event_filter.custom(event_name + "." + condition.to_query_condition())
        
        query = event_filter.to_query()
        
        # Store subscription info
        subscription_info = {
            "query": query,
            "filter": event_filter,
            "event_name": event_name,
            "event_class": event_class,
            "event_attribute_conditions": event_attribute_conditions,
            "active": False,
            "subscription_type": "TypedNewBlockEvents"
        }
        
        self.subscriptions[subscription_id] = subscription_info
        
        # Store callback
        if subscription_id not in self.callbacks:
            self.callbacks[subscription_id] = []
        self.callbacks[subscription_id].append(callback)
        
        # Send subscription if connected
        if self.websocket and not self.websocket.close_code:
            await self._send_subscription(subscription_id, query)
        else:
            logger.warning("❌ WebSocket not connected, subscription will be sent when connected")
        
        logger.debug(f"✅ Completed typed subscription: {event_name} -> {event_class.__name__} (ID: {subscription_id})")
        return subscription_id
    
    def _get_event_type_from_class(self, event_class: Type[betterproto2.Message]) -> Optional[str]:
        """Get the event type string from a protobuf class."""
        
        # First try direct class match
        for event_type, registered_class in self.event_registry._event_map.items():
            logger.debug(f"  Checking {event_type} -> {registered_class}")
            if registered_class == event_class:
                return event_type
        
        # Try matching by class name if no exact match
        class_name = event_class.__name__
        
        for event_type, registered_class in self.event_registry._event_map.items():
            if registered_class.__name__ == class_name:
                return event_type
        
        logger.warning(f"❌ No event type found for class {event_class}")
        return None


