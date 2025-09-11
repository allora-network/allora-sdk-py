import importlib
import inspect
import json
import pkgutil
import logging
from typing import Any, Callable, Dict, List, Optional, Type

import betterproto2

logger = logging.getLogger("allora_sdk")


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
        """Auto-discover Event* classes from all allora_sdk.protos subpackages."""
        base_pkg_name = "allora_sdk.protos"
        try:
            base_pkg = importlib.import_module(base_pkg_name)
        except ImportError as err:
            print(f"ImportError: {err}")
            return

        for modinfo in pkgutil.walk_packages(base_pkg.__path__, base_pkg.__name__ + "."):
            module_name = modinfo.name
            try:
                module = importlib.import_module(module_name)
            except ImportError as err:
                print(f"ImportError: {err}")
                continue
            except Exception as err:
                print(f"Error importing {module_name}: {err}")
                continue

            try:
                event_classes = [
                    (name, obj) for name, obj in inspect.getmembers(module)
                    if (
                        inspect.isclass(obj)
                        # and name.startswith("Event")
                        and hasattr(obj, "__annotations__")
                        and issubclass(obj, betterproto2.Message)
                    )
                ]
            except Exception:
                event_classes = []

            if not event_classes:
                continue

            # Build event type prefix from module path relative to base package
            try:
                if module_name.startswith(base_pkg_name + "."):
                    rel_module = module_name[len(base_pkg_name) + 1 :]
                else:
                    rel_module = module_name
            except Exception:
                rel_module = module_name

            for name, obj in event_classes:
                event_type = f"{rel_module}.{name}"
                # Prefer first registration; warn on conflicting duplicate
                if event_type in self._event_map and self._event_map[event_type] is not obj:
                    logger.debug(f"Duplicate event type {event_type}; keeping first registration")
                    continue
                self._event_map[event_type] = obj

    def get_event_class(self, event_type: str) -> Optional[Type[betterproto2.Message]]:
        """Get the protobuf class for an event type string."""
        return self._event_map.get(event_type)


class ParseError(Exception):
    pass


class EventMarshaler:
    """
    Marshals generic JSON event attributes to protobuf Event instances with schema-driven parsing.
    The generic format is what Tendermint provides over WebSocket subscriptions.

    e.g.
    {
        "type": "emissions.v9.EventReputerSubmissionWindowOpened",
        "attributes": [
            {"key": "topic_id", "value": "42"},
            {"key": "nonce", "value": "12893851"},
            {"key": "window_start_height", "value": "123456"},
            {"key": "window_end_height", "value": "123466"},
        ]
    }

    becomes

    emissions.v9.EventReputerSubmissionWindowOpened(
        topic_id=42,
        nonce=12893851,
        window_start_height=123456,
        window_end_height=123466,
    )
    """

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
            logger.debug("Event JSON missing 'type' field")
            return None

        event_class = self.registry.get_event_class(event_type)
        if not event_class:
            logger.debug(f"No protobuf class registered for event type: {event_type}")
            return None

        attributes = event_json.get('attributes', [])

        # Build parser table for the target event class
        resolved_annotations = self._get_resolved_annotations(event_class)
        field_values = self._parse_attributes(attributes, event_class, resolved_annotations)
        logger.debug(f"Parsed field values: {field_values}")

        try:
            # Create protobuf instance with parsed field values
            instance = event_class(**field_values)
            return instance
        except Exception as e:
            logger.error(f"Failed to create {event_class.__name__} instance: {e}")
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
                if expected_type is int and isinstance(value, str) and self._is_int(value):
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

    def _is_int(self, value: str):
        return value.isdigit() or (value.startswith('-') and value[1:].isdigit())

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
