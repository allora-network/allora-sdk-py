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
from typing import Dict, List, Callable, Any, Literal, Optional, Set, Union, Type, TypeVar
import websockets
from websockets import ClientConnection
import aiohttp
import betterproto

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

T = TypeVar('T', bound=betterproto.Message)


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
    _event_map: Dict[str, Type[betterproto.Message]] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._event_map:
            logger.info("🔍 Discovering event classes from protobuf modules...")
            self._discover_event_classes()
            logger.info(f"✅ Event registry initialized with {len(self._event_map)} event types")
    
    def _discover_event_classes(self):
        """Auto-discover Event classes from emissions protobuf modules."""
        versions = ['v1', 'v2', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8', 'v9']
        
        for version in versions:
            try:
                module_name = f"allora_sdk.protobuf_client.proto.emissions.{version}"
                module = importlib.import_module(module_name)
                
                # Find all Event classes in the module
                for name, obj in inspect.getmembers(module):
                    if (inspect.isclass(obj) and 
                        name.startswith('Event') and 
                        hasattr(obj, '__annotations__') and
                        issubclass(obj, betterproto.Message)):
                        
                        # Create the full event type name (e.g., "emissions.v9.EventScoresSet")
                        event_type = f"emissions.{version}.{name}"
                        self._event_map[event_type] = obj
                        logger.debug(f"Registered event type: {event_type} -> {obj}")
                        
            except ImportError as e:
                logger.warning(f"Could not import {module_name}: {e}")
                continue
        
        logger.info(f"Discovered {len(self._event_map)} event types across all versions")
    
    def get_event_class(self, event_type: str) -> Optional[Type[betterproto.Message]]:
        """Get the protobuf class for an event type string."""
        return self._event_map.get(event_type)
    
    def list_event_types(self) -> List[str]:
        """List all registered event types."""
        return list(self._event_map.keys())
    
    def is_registered(self, event_type: str) -> bool:
        """Check if an event type is registered."""
        return event_type in self._event_map

class EventMarshaler:
    """Marshals JSON event attributes to protobuf Event instances."""
    
    def __init__(self, registry: EventRegistry):
        self.registry = registry
    
    def marshal_event(self, event_json: Dict[str, Any]) -> Optional[betterproto.Message]:
        """
        Convert a JSON event to a protobuf Event instance.
        
        Args:
            event_json: JSON event with 'type' and 'attributes' fields
            
        Returns:
            Protobuf event instance or None if type not registered
        """
        event_type = event_json.get('type')
        logger.info(f"🔄 Marshaling event: {event_type}")
        
        if not event_type:
            logger.warning("❌ Event JSON missing 'type' field")
            return None
        
        event_class = self.registry.get_event_class(event_type)
        if not event_class:
            logger.warning(f"❌ No protobuf class registered for event type: {event_type}")
            return None
        
        logger.info(f"✅ Found protobuf class: {event_class.__name__}")
        
        attributes = event_json.get('attributes', [])
        logger.info(f"📊 Processing {len(attributes)} attributes")
        
        field_values = self._parse_attributes(attributes, event_class)
        logger.info(f"🔧 Parsed field values: {field_values}")
        
        try:
            # Create protobuf instance with parsed field values
            instance = event_class(**field_values)
            logger.info(f"✅ Successfully created {event_class.__name__} instance")
            return instance
        except Exception as e:
            logger.error(f"❌ Failed to create {event_class.__name__} instance: {e}")
            logger.error(f"   Field values: {field_values}")
            return None
    
    def _parse_attributes(self, attributes: List[Dict[str, Any]], event_class: Type[betterproto.Message]) -> Dict[str, Any]:
        """Parse JSON attributes array into protobuf field values."""
        field_values = {}
        
        # Get field annotations from the protobuf class
        field_annotations = getattr(event_class, '__annotations__', {})
        logger.info(f"📝 Protobuf class fields: {list(field_annotations.keys())}")
        
        for attr in attributes:
            key = attr.get('key')
            value = attr.get('value')
            
            if not key or value is None:
                continue
            
            # Skip fields that don't exist in the protobuf class (metadata fields)
            if key not in field_annotations:
                logger.info(f"⚠️ Skipping metadata field '{key}' (not in protobuf schema)")
                continue
            
            # Convert JSON string value to appropriate Python type
            parsed_value = self._parse_attribute_value(key, value, field_annotations)
            if parsed_value is not None:
                field_values[key] = parsed_value
        
        return field_values
    
    def _parse_attribute_value(self, field_name: str, json_value: str, field_annotations: Dict[str, Any]) -> Any:
        """Parse a single attribute value from JSON string to Python type."""
        try:
            # Get the expected field type from protobuf annotations
            field_type = field_annotations.get(field_name)
            logger.debug(f"🔍 Field '{field_name}' type: {field_type}, value: '{json_value}'")
            
            # Handle JSON-encoded values (common in blockchain events)
            if json_value.startswith('"') and json_value.endswith('"'):
                # Simple quoted string - remove quotes first
                unquoted_value = json_value[1:-1]
                
                # If field type is int and the unquoted value is numeric, convert to int
                if field_type is int and (unquoted_value.isdigit() or (unquoted_value.startswith('-') and unquoted_value[1:].isdigit())):
                    return int(unquoted_value)
                else:
                    return unquoted_value
                    
            elif json_value.startswith('[') and json_value.endswith(']'):
                # JSON array - parse it
                parsed = json.loads(json_value)
                return parsed if isinstance(parsed, list) else [parsed]
            elif json_value in ('true', 'false'):
                # Boolean values
                return json_value == 'true'
            elif json_value.isdigit() or (json_value.startswith('-') and json_value[1:].isdigit()):
                # Integer values
                return int(json_value)
            elif self._is_float(json_value):
                # Float values  
                return float(json_value)
            else:
                # Try parsing as JSON first, fall back to string
                try:
                    return json.loads(json_value)
                except json.JSONDecodeError:
                    return json_value
                    
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse attribute {field_name}='{json_value}': {e}")
            return json_value  # Return as string if parsing fails
    
    def _is_float(self, value: str) -> bool:
        """Check if string represents a float."""
        try:
            float(value)
            return '.' in value or 'e' in value.lower()
        except ValueError:
            return False

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
    

class AlloraWebsocketSubscriber:
    """
    WebSocket-based event subscriber for Allora blockchain events.
    
    Provides real-time event streaming with automatic reconnection,
    filtering, and callback management.
    """
    
    def __init__(self, client):
        """Initialize event subscriber with Allora client."""
        self.client = client
        self.websocket: Optional[ClientConnection] = None
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
        """Start the event subscription service."""
        if self.running:
            logger.warning("Event subscriber already running")
            return
        
        self.running = True
        await self._connect()

    async def _ensure_started(self):
        """Ensure the event subscription service is started (idiomatic auto-start)."""
        if not self.running:
            logger.info("🚀 Auto-starting event subscription service...")
            await self.start()
    
    async def stop(self):
        """Stop the event subscription service."""
        self.running = False
        
        # Unsubscribe from all subscriptions
        for subscription_id in list(self.subscriptions.keys()):
            await self._unsubscribe(subscription_id)
        
        # Close WebSocket connection
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        
        logger.info("Event subscriber stopped")
    
    async def subscribe(
        self,
        event_filter: EventFilter,
        callback: Callable[[Dict[str, Any]], None],
        subscription_id: Optional[str] = None
    ) -> str:
        """
        Subscribe to events matching the filter.
        
        Args:
            event_filter: Filter defining which events to receive
            callback: Function to call when matching events arrive
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
        
        logger.info(f"Subscribed to events: {query} (ID: {subscription_id})")
        return subscription_id
    
    async def unsubscribe(self, subscription_id: str):
        """Unsubscribe from events."""
        if subscription_id not in self.subscriptions:
            logger.warning(f"Subscription {subscription_id} not found")
            return
        
        await self._unsubscribe(subscription_id)
        
        # Remove from local storage
        self.subscriptions.pop(subscription_id, None)
        self.callbacks.pop(subscription_id, None)
        
        logger.info(f"Unsubscribed from {subscription_id}")
    
    async def _connect(self):
        """Establish WebSocket connection."""
        attempts = 0
        while attempts < self.max_reconnect_attempts and self.running:
            try:
                logger.info(f"Connecting to {self.client.config.websocket_endpoint}")
                self.websocket = await websockets.connect(
                    self.client.config.websocket_endpoint,
                    ping_interval=20,
                    ping_timeout=10
                )
                logger.info("WebSocket connected")
                
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
            logger.error("WebSocket not connected")
            return
        
        request = {
            "jsonrpc": "2.0",
            "method": "subscribe", 
            "id": subscription_id,
            "params": {"query": query}
        }
        
        try:
            logger.info(f"📤 Sending subscription request: {json.dumps(request, indent=2)}")
            await self.websocket.send(json.dumps(request))
            # Don't mark as active here - wait for confirmation
            logger.info(f"✅ Successfully sent subscription request for {subscription_id}")
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
            logger.debug(f"Sent unsubscribe: {request}")
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
                
                # Wait for message with timeout
                try:
                    message = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=30.0
                    )
                    await self._handle_message(message)
                    
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
                await asyncio.sleep(1.0)
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            data2 = json.dumps(data, indent=4)  # Pretty print JSON for debugging
            logger.debug(f"Received WebSocket message: {data2}")
            
            # Enhanced logging for debugging
            message_id = data.get("id")
            has_result = "result" in data
            has_result_data = data.get("result", {}).get("data") is not None
            has_events = data.get("result", {}).get("data", {}).get("value", {}).get("events") is not None if has_result_data else False
            
            logger.info(f"🔍 Message analysis: ID={message_id}, has_result={has_result}, has_result_data={has_result_data}, has_events={has_events}")
            
            # Handle subscription confirmations
            if data.get("result", {}).get("data") is None and "id" in data:
                subscription_id = data["id"]
                logger.info(f"✅ Identified as subscription confirmation for: {subscription_id}")
                if subscription_id in self.subscriptions:
                    logger.info(f"✅ Subscription {subscription_id} confirmed and found in subscriptions")
                    # Mark subscription as active
                    self.subscriptions[subscription_id]["active"] = True
                else:
                    logger.warning(f"❌ Subscription {subscription_id} confirmed but not found in subscriptions!")
                return

            logger.info("🧩 Processing as event message...")
            
            # Try to parse as structured JSONRPCResponse
            events = None
            message_id = data.get("id")
            
            try:
                msg = JSONRPCResponse.model_validate(data)
                logger.info(f"✅ Parsed structured response successfully")
                
                # Extract events from NewBlockEvents
                if (isinstance(msg.result, JSONRPCQueryResult) and 
                    isinstance(msg.result.data, NewBlockEventsDataFrame)):
                    events = msg.result.data.value.events
                    logger.info(f"📦 Extracted {len(events)} events from structured response")
                else:
                    logger.info(f"❌ Structured response doesn't match expected NewBlockEvents format")
                    
            except Exception as parse_error:
                logger.info(f"❌ Failed to parse as structured response: {parse_error}")
                # Fall back to manual extraction
                
                # Manual extraction as fallback
                events = data.get("result", {}).get("data", {}).get("value", {}).get("events")
                if events is not None:
                    logger.info(f"📦 Manual extraction found {len(events)} events")
                else:
                    logger.warning("❌ Manual extraction found no events")
            
            # Dispatch events if found
            if events is not None:
                logger.info(f"🚀 Dispatching {len(events)} events with message_id={message_id}")
                await self._dispatch_events(events, message_id)
            else:
                logger.warning("❌ No events found to dispatch")
            
            # Handle errors
            if "error" in data:
                logger.error(f"Subscription error: {data['error']}")
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
    async def _dispatch_events(self, event_data: List[Dict[str, Any]], target_subscription_id: Optional[str] = None):
        """Dispatch event to registered callbacks."""
        logger.info(f"📨 _dispatch_events called with {len(event_data)} events, target_id={target_subscription_id}")
        
        # Log subscription and callback status
        logger.info(f"📋 Total subscriptions: {len(self.subscriptions)}")
        logger.info(f"📞 Total callbacks: {len(self.callbacks)}")
        
        # Find all subscriptions that should receive these events
        matching_subscriptions = []
        
        if target_subscription_id:
            # Find the target subscription's query
            target_query = None
            if target_subscription_id in self.subscriptions:
                target_query = self.subscriptions[target_subscription_id]["query"]
                logger.info(f"🔍 Target subscription query: {target_query}")
                
                # Find ALL subscriptions with the same query (including the target)
                for subscription_id, subscription_info in self.subscriptions.items():
                    if (subscription_info.get("query") == target_query and 
                        subscription_info.get("active", False) and
                        subscription_id in self.callbacks):
                        matching_subscriptions.append(subscription_id)
                        logger.info(f"🎯 Found matching subscription: {subscription_id}")
            
            if not matching_subscriptions:
                logger.warning(f"❌ No active subscriptions found for target {target_subscription_id}")
        else:
            # Fallback: dispatch to all active subscriptions
            for subscription_id in self.callbacks.keys():
                if subscription_id in self.subscriptions:
                    is_active = self.subscriptions[subscription_id].get("active", False)
                    if is_active:
                        matching_subscriptions.append(subscription_id)
                    logger.info(f"📊 Subscription {subscription_id}: active={is_active}")
        
        logger.info(f"🔄 Dispatching to {len(matching_subscriptions)} matching subscriptions: {matching_subscriptions}")
        
        for subscription_id in matching_subscriptions:
            await self._dispatch_to_subscription(subscription_id, event_data)
    
    async def _dispatch_to_subscription(self, subscription_id: str, event_data: List[Dict[str, Any]]):
        """Dispatch events to a specific subscription, applying filters as needed."""
        logger.info(f"🏃 _dispatch_to_subscription called for {subscription_id}")
        
        subscription_info = self.subscriptions.get(subscription_id)
        if not subscription_info:
            logger.warning(f"❌ No subscription info found for {subscription_id}")
            return
        
        callbacks = self.callbacks.get(subscription_id, [])
        subscription_type = subscription_info.get("subscription_type", "tendermint_query")
        
        logger.info(f"📋 Subscription {subscription_id}: type={subscription_type}, callbacks={len(callbacks)}")
        
        if not callbacks:
            logger.warning(f"❌ No callbacks registered for subscription {subscription_id}")
            return
        
        if subscription_type == "NewBlockEvents":
            # Apply client-side event name filtering (dual-level filtering)
            event_name = subscription_info.get("event_name")
            logger.info(f"🔍 NewBlockEvents filtering: event_name={event_name}, event_data_count={len(event_data)}")
            
            if event_name and event_data:
                # Filter events by event name (the Tendermint query already filtered by attributes)
                filtered_events = []
                for i, event in enumerate(event_data):
                    event_type = event.get("type")
                    logger.info(f"🧪 Event {i}: type='{event_type}' vs expected='{event_name}'")
                    if event_type == event_name:
                        filtered_events.append(event)
                        logger.info(f"✅ Event {i} matched!")
                
                logger.info(f"🎯 Filtered {len(filtered_events)} matching events from {len(event_data)} total")
                
                # Only call callbacks if we have matching events
                if filtered_events:
                    for j, callback in enumerate(callbacks):
                        logger.info(f"📞 Calling callback {j} for {len(filtered_events)} events")
                        try:
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(None, callback, filtered_events)
                            logger.info(f"✅ Successfully called block events callback {j} for subscription {subscription_id}")
                        except Exception as e:
                            logger.error(f"❌ Block events callback error for {subscription_id}: {e}")
                else:
                    logger.warning(f"❌ No events matched event_name '{event_name}' for subscription {subscription_id}")
            else:
                logger.warning(f"❌ Missing event_name or event_data: name={event_name}, data={bool(event_data)}")
                    
        elif subscription_type == "TypedNewBlockEvents":
            # Apply client-side filtering and marshal to protobuf instances
            event_name = subscription_info.get("event_name")
            event_class = subscription_info.get("event_class")
            logger.info(f"🔍 TypedNewBlockEvents filtering: event_name={event_name}, event_class={event_class}")
            
            if event_name and event_class and event_data:
                # Filter events by event name and marshal to protobuf instances
                typed_events = []
                for i, event in enumerate(event_data):
                    event_type = event.get("type")
                    logger.info(f"🧪 Typed Event {i}: type='{event_type}' vs expected='{event_name}'")
                    if event_type == event_name:
                        # Marshal JSON event to protobuf instance
                        logger.info(f"🔄 Marshaling event {i} to protobuf...")
                        typed_event = self.event_marshaler.marshal_event(event)
                        if typed_event:
                            typed_events.append(typed_event)
                            logger.info(f"✅ Successfully marshaled event {i}")
                        else:
                            logger.warning(f"❌ Failed to marshal event {i}: {event.get('type')}")
                
                logger.info(f"🎯 Marshaled {len(typed_events)} typed events from {len(event_data)} total")
                
                # Only call callbacks if we have successfully marshaled events
                if typed_events:
                    for j, callback in enumerate(callbacks):
                        logger.info(f"📞 Calling typed callback {j} for {len(typed_events)} events")
                        try:
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(None, callback, typed_events)
                            logger.info(f"✅ Successfully called typed block events callback {j} for subscription {subscription_id}")
                        except Exception as e:
                            logger.error(f"❌ Typed block events callback error for {subscription_id}: {e}")
                else:
                    logger.warning(f"❌ No events successfully marshaled for subscription {subscription_id}")
            else:
                logger.warning(f"❌ Missing requirements: name={event_name}, class={event_class}, data={bool(event_data)}")
                    
        else:
            # Regular tendermint query subscription - pass all events
            for callback in callbacks:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, callback, event_data)
                    logger.debug(f"Called callback for subscription {subscription_id}")
                except Exception as e:
                    logger.error(f"Callback error for {subscription_id}: {e}")
    
    # Convenience methods for common subscriptions
    
    async def subscribe_to_new_blocks(self, callback: Callable[[Dict[str, Any]], None]) -> str:
        """Subscribe to new block events."""
        return await self.subscribe(EventFilter.new_blocks(), callback)
    
    async def subscribe_to_transactions(self, callback: Callable[[Dict[str, Any]], None]) -> str:
        """Subscribe to transaction events."""
        return await self.subscribe(EventFilter.transactions(), callback)
    
    async def subscribe_to_address_activity(self, address: str, callback: Callable[[Dict[str, Any]], None]) -> str:
        """Subscribe to activity for a specific address."""
        event_filter = EventFilter.transactions().sender(address)
        return await self.subscribe(event_filter, callback)
    
    async def subscribe_new_block_events(
        self, 
        event_name: str,
        event_attribute_conditions: List[EventAttributeCondition], 
        callback: Callable[[List[Dict[str, Any]]], None],
        subscription_id: Optional[str] = None
    ) -> str:
        """
        Subscribe to specific events within NewBlockEvents.
        
        Args:
            event_name: The specific event type to filter for (e.g., "emissions.v9.EventEMAScoresSet")
            event_attribute_conditions: List of attribute conditions to apply
            callback: Function to call with filtered events (list of matching events)
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
        
        logger.info(f"Subscribed to filtered block events: {event_name} with conditions {event_attribute_conditions} (ID: {subscription_id})")
        return subscription_id
    
    async def subscribe_new_block_events_typed(
        self, 
        event_class: Type[T],
        event_attribute_conditions: List[EventAttributeCondition], 
        callback: Callable[[List[T]], None],
        subscription_id: Optional[str] = None
    ) -> str:
        """
        Subscribe to specific events within NewBlockEvents with typed protobuf callbacks.
        
        Args:
            event_class: The protobuf Event class to subscribe to (e.g., EventScoresSet)
            event_attribute_conditions: List of attribute conditions to apply
            callback: Function to call with typed protobuf events (list of protobuf instances)
            subscription_id: Optional custom subscription ID
            
        Returns:
            Subscription ID for managing the subscription
        """
        # Auto-start the event subscription service if not already running
        await self._ensure_started()
        
        logger.info(f"🔧 Creating typed subscription for {event_class}")
        
        if not subscription_id:
            self._subscription_id_counter += 1
            subscription_id = f"typed_block_events_{self._subscription_id_counter}"
        
        logger.info(f"🆔 Generated subscription ID: {subscription_id}")
        
        # Extract event name from class (e.g., EventScoresSet -> emissions.v9.EventScoresSet)
        event_name = self._get_event_type_from_class(event_class)
        logger.info(f"🔍 Event name resolution: {event_class.__name__} -> {event_name}")
        
        if not event_name:
            logger.error(f"❌ Could not determine event type for class {event_class.__name__}")
            logger.info(f"📋 Available event types: {list(self.event_registry._event_map.keys())[:10]}...")  # Show first 10
            raise ValueError(f"Could not determine event type for class {event_class.__name__}")
        
        # Construct EventFilter with NewBlockEvents and attribute conditions
        event_filter = EventFilter().event_type('NewBlockEvents')
        for condition in event_attribute_conditions:
            event_filter.custom(event_name + "." + condition.to_query_condition())
        
        query = event_filter.to_query()
        logger.info(f"🔍 Generated query: {query}")
        
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
        logger.info(f"💾 Stored typed subscription: {subscription_id} -> {subscription_info}")
        
        # Store callback
        if subscription_id not in self.callbacks:
            self.callbacks[subscription_id] = []
        self.callbacks[subscription_id].append(callback)
        logger.info(f"📞 Stored callback for {subscription_id}, total callbacks: {len(self.callbacks[subscription_id])}")
        
        # Send subscription if connected
        if self.websocket and not self.websocket.close_code:
            logger.info(f"📤 Sending typed subscription request...")
            await self._send_subscription(subscription_id, query)
        else:
            logger.warning("❌ WebSocket not connected, subscription will be sent when connected")
        
        logger.info(f"✅ Completed typed subscription: {event_name} -> {event_class.__name__} (ID: {subscription_id})")
        return subscription_id
    
    def _get_event_type_from_class(self, event_class: Type[betterproto.Message]) -> Optional[str]:
        """Get the event type string from a protobuf class."""
        logger.info(f"🔍 Looking for event type for class: {event_class}")
        logger.info(f"📊 Registry has {len(self.event_registry._event_map)} registered types")
        
        for event_type, registered_class in self.event_registry._event_map.items():
            logger.debug(f"  Checking {event_type} -> {registered_class}")
            if registered_class == event_class:
                logger.info(f"✅ Found match: {event_class} -> {event_type}")
                return event_type
        
        logger.warning(f"❌ No event type found for class {event_class}")
        logger.info(f"📋 Available registered classes: {[cls.__name__ for cls in self.event_registry._event_map.values()][:10]}")
        return None


# # Event handler decorators and utilities

# class EventHandler:
#     """Decorator for creating event handlers."""
    
#     def __init__(self, event_filter: EventFilter):
#         self.event_filter = event_filter
#         self.handlers: List[Callable] = []
    
#     def __call__(self, func: Callable):
#         """Register function as event handler."""
#         self.handlers.append(func)
#         return func
    
#     async def register_with_subscriber(self, subscriber: AlloraWebsocketSubscriber):
#         """Register all handlers with event subscriber."""
#         for handler in self.handlers:
#             await subscriber.subscribe(self.event_filter, handler)


