"""
Allora Event Subscription System

This module provides WebSocket-based event subscription functionality
for monitoring Allora blockchain events in real-time.
"""

import asyncio
import json
import logging
import importlib
import pkgutil
import inspect
import time
from typing import AsyncIterable, Awaitable, Dict, Iterable, List, Callable, Any, Literal, Optional, Union, Type, TypeVar, Protocol, runtime_checkable
import websockets
import traceback
import betterproto2
from pydantic import BaseModel
from .event_utils import EventMarshaler, EventRegistry

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


class EventFilter:
    """Event filter for subscription queries."""

    def __init__(self):
        self.conditions: List[str] = []

    def event_type(self, event_type: str):
        """Filter by event type (e.g., 'NewBlock', 'Tx')."""
        self.conditions.append(f"tm.event='{event_type}'")
        return self

    def message_action(self, action: str):
        """Filter by message action."""
        self.conditions.append(f"message.action='{action}'")
        return self

    def message_module(self, module: str):
        """Filter by message module."""
        self.conditions.append(f"message.module='{module}'")
        return self

    def attribute(self, key: str, value: Union[str, int, float]):
        """Filter by custom attribute."""
        if isinstance(value, str):
            self.conditions.append(f"{key}='{value}'")
        else:
            self.conditions.append(f"{key}={value}")
        return self

    def custom(self, query: str):
        self.conditions.append(query)
        return self

    def sender(self, address: str):
        """Filter by sender address."""
        self.conditions.append(f"message.sender='{address}'")
        return self

    def to_query(self) -> str:
        """Convert filter to Tendermint query string."""
        if not self.conditions:
            return "tm.event='NewBlock'"
        return " AND ".join(self.conditions)

    @staticmethod
    def new_blocks():
        """Filter for new block events."""
        return EventFilter().event_type('NewBlock')

    @staticmethod
    def transactions():
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
        self._connect_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._event_task: Optional[asyncio.Task] = None
        # Dedupe recent events per subscription id: key -> timestamp
        self._recent_event_keys: Dict[str, Dict[str, float]] = {}
        self.dedupe_ttl_seconds = 60.0
        
        # Initialize event registry and marshaler for typed subscriptions
        self.event_registry = EventRegistry()
        self.event_marshaler = EventMarshaler(self.event_registry)
        
    async def start(self):
        if self.running:
            logger.warning("Event subscriber already running")
            return
        
        self.running = True

        self._event_task = asyncio.create_task(self._event_loop())


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
        
        async with self._state_lock:
            pending_unsubs = list(self.subscriptions.keys())
        for subscription_id in pending_unsubs:
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
        async with self._state_lock:
            self.subscriptions[subscription_id] = {
                "query": query,
                "filter": event_filter,
                "active": False,
                "sent": False,
                "subscription_type": "tendermint_query"
            }
            # Store callback
            if subscription_id not in self.callbacks:
                self.callbacks[subscription_id] = []
            self.callbacks[subscription_id].append(callback)
        
        logger.debug(f"Subscribed to events: {query} (ID: {subscription_id})")
        return subscription_id
    
    async def unsubscribe(self, subscription_id: str):
        """Unsubscribe from events."""
        async with self._state_lock:
            if subscription_id not in self.subscriptions:
                logger.debug(f"Subscription {subscription_id} not found")
                return
        
        await self._unsubscribe(subscription_id)
        
        # Remove from local storage
        async with self._state_lock:
            self.subscriptions.pop(subscription_id, None)
            self.callbacks.pop(subscription_id, None)
        
        logger.debug(f"Unsubscribed from {subscription_id}")
    
    async def _connect(self):
        """Establish WebSocket connection."""
        async with self._connect_lock:
            attempts = 0
            while attempts < self.max_reconnect_attempts and self.running:
                try:
                    logger.debug(f"Connecting to {self.url}")
                    self.websocket = await self.connect_fn(self.url)
                    logger.debug("WebSocket connected")
                    
                    # Snapshot subscriptions to send without holding the lock during awaits
                    async with self._state_lock:
                        items = list(self.subscriptions.items())
                        # Reset sent/active so they get re-sent after reconnect
                        for _sid, info in items:
                            info["sent"] = False
                            info["active"] = False
                    
                    for subscription_id, info in items:
                        await self._send_subscription(subscription_id, info["query"])
                        async with self._state_lock:
                            if subscription_id in self.subscriptions:
                                self.subscriptions[subscription_id]["sent"] = True
                    
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
        
        async with self._state_lock:
            query = self.subscriptions.get(subscription_id, {}).get("query")
        if query is None:
            return
        request = {
            "jsonrpc": "2.0",
            "method": "unsubscribe",
            "id": subscription_id,
            "params": {"query": query}
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
                    logger.info("Websocket connected")
                    continue
                
                try:
                    message = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=None
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
                async with self._state_lock:
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
        async with self._state_lock:
            if target_subscription_id not in self.subscriptions:
                return
            # Only dispatch to the exact subscription id
            active = self.subscriptions[target_subscription_id].get("active", False)
        if not active:
            return
        await self._dispatch_to_subscription(target_subscription_id, event_data, block_height)
    
    async def _dispatch_to_subscription(
        self,
        subscription_id: str,
        event_data: List[Dict[str, Any]],
        block_height: Optional[int] = None,
    ) -> None:
        """Dispatch events to a specific subscription with filtering and type marshaling."""
        if not event_data:
            return
        
        # Snapshot subscription info and callbacks under lock
        async with self._state_lock:
            subscription_info = self.subscriptions.get(subscription_id)
            if not subscription_info:
                return
            callbacks = list(self.callbacks.get(subscription_id, []))
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
    
    def _get_expected_field_type(self, message_cls: Optional[Type[betterproto2.Message]], field_name: str) -> Optional[Type[Any]]:
        if message_cls is None:
            return None
        annotations = self.event_marshaler._get_resolved_annotations(message_cls)
        return annotations.get(field_name)
    
    def _strip_outer_quotes(self, s: Any) -> Any:
        if not isinstance(s, str):
            return s
        return self.event_marshaler._strip_quotes(s)
    
    def _coerce_value_to_type(self, value: Any, expected_type: Optional[Type[Any]]) -> Any:
        try:
            import typing as _typing
            origin = _typing.get_origin(expected_type)
            args = _typing.get_args(expected_type) if expected_type is not None else ()
        except Exception:
            origin = None
            args = ()
        
        v = self._strip_outer_quotes(value)
        
        if expected_type is None:
            return v
        
        if origin in (list, List) and args:
            elem_type = args[0]
            if isinstance(v, list):
                return [self._coerce_value_to_type(elem, elem_type) for elem in v]
            return [self._coerce_value_to_type(v, elem_type)]
        
        if expected_type is int:
            if isinstance(v, int):
                return v
            if isinstance(v, str):
                s = v
                if s.isdigit() or (s.startswith('-') and s[1:].isdigit()):
                    return int(s)
            return v
        if expected_type is float:
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v)
                except Exception:
                    return v
            return v
        if expected_type is bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                if v == 'true':
                    return True
                if v == 'false':
                    return False
            return v
        if expected_type is str:
            if isinstance(v, str):
                return v
            return str(v)
        
        return v
    
    def _evaluate_operator(self, left: Any, operator: str, right: Any) -> bool:
        def _cmp(a: Any, b: Any) -> bool:
            if operator == '=':
                return a == b
            if operator == '<':
                try:
                    return a < b
                except Exception:
                    return False
            if operator == '<=':
                try:
                    return a <= b
                except Exception:
                    return False
            if operator == '>':
                try:
                    return a > b
                except Exception:
                    return False
            if operator == '>=':
                try:
                    return a >= b
                except Exception:
                    return False
            if operator == 'CONTAINS':
                if isinstance(a, list):
                    return b in a
                if isinstance(a, str) and isinstance(b, str):
                    return b in a
                return False
            return False
        
        if operator == 'EXISTS':
            return left is not None
        
        if isinstance(left, list):
            for elem in left:
                if _cmp(elem, right):
                    return True
            return False
        return _cmp(left, right)
    
    def _event_matches_conditions_untyped(
        self,
        event_json: Dict[str, Any],
        event_name: str,
        conditions: List[EventAttributeCondition],
    ) -> bool:
        attributes = event_json.get('attributes') or []
        values_by_key: Dict[str, List[Any]] = {}
        for a in attributes:
            k = a.get('key')
            if k is None:
                continue
            values_by_key.setdefault(k, []).append(a.get('value'))
        
        event_cls = self.event_registry.get_event_class(event_name)
        
        for cond in conditions:
            expected_type = self._get_expected_field_type(event_cls, cond.attribute_name)
            raw_values = values_by_key.get(cond.attribute_name)
            if cond.operator == 'EXISTS':
                if not raw_values:
                    return False
                continue
            if not raw_values:
                return False
            coerced_values = [self._coerce_value_to_type(rv, expected_type) for rv in raw_values]
            cond_right = self._coerce_value_to_type(cond.value, expected_type)
            if not self._evaluate_operator(coerced_values, cond.operator, cond_right):
                return False
        return True
    
    def _event_matches_conditions_typed(
        self,
        event_obj: betterproto2.Message,
        event_cls: Type[betterproto2.Message],
        conditions: List[EventAttributeCondition],
    ) -> bool:
        for cond in conditions:
            expected_type = self._get_expected_field_type(event_cls, cond.attribute_name)
            left = getattr(event_obj, cond.attribute_name, None)
            if cond.operator == 'EXISTS':
                if left is None:
                    return False
                continue
            right = self._coerce_value_to_type(cond.value, expected_type)
            if not self._evaluate_operator(left, cond.operator, right):
                return False
        return True
    
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
        conditions: List[EventAttributeCondition] = subscription_info.get("event_attribute_conditions") or []
        if conditions:
            events = [e for e in events if self._event_matches_conditions_untyped(e, event_name, conditions)]
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
        conditions: List[EventAttributeCondition] = subscription_info.get("event_attribute_conditions") or []
        if conditions and events:
            events = [e for e in events if self._event_matches_conditions_typed(e, event_class, conditions)]
        
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
        # Basic per-subscription dedupe by block height and event content
        now = time.time()
        async with self._state_lock:
            recent = self._recent_event_keys.setdefault(subscription_id, {})
            # prune old
            cutoff = now - self.dedupe_ttl_seconds
            for k, ts in list(recent.items()):
                if ts < cutoff:
                    recent.pop(k, None)
        
        def _event_key(e: Any) -> str:
            try:
                if isinstance(e, dict):
                    s = json.dumps(e, sort_keys=True)
                else:
                    s = str(e)
            except Exception:
                s = str(e)
            return s
        
        unique_events: List[Any] = []
        async with self._state_lock:
            recent = self._recent_event_keys.setdefault(subscription_id, {})
            for e in events:
                key = f"{block_height}:{_event_key(e)}"
                if key in recent:
                    continue
                recent[key] = now
                unique_events.append(e)
        
        tasks: List[asyncio.Future] = []
        loop = asyncio.get_running_loop()
        for callback in callbacks:
            for event in unique_events:
                if asyncio.iscoroutinefunction(callback):
                    tasks.append(asyncio.create_task(callback(event, block_height)))
                else:
                    tasks.append(loop.run_in_executor(None, callback, event, block_height))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error(f"Callback error for {subscription_id}: {r}")
    
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


