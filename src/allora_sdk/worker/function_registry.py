"""Callable registry and import-string resolver for worker runner."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable


class FunctionRegistry:
    """Simple registry used by config-driven worker execution."""

    def __init__(self) -> None:
        self._callbacks: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, callback: Callable[..., Any]) -> None:
        """Register a callback under a stable symbolic name."""
        name = name.strip()
        if not name:
            raise ValueError("registry name cannot be empty")
        self._callbacks[name] = callback

    def resolve(self, ref: str) -> Callable[..., Any]:
        """Resolve a callable from `registry:name` or `module:function` ref."""
        if ref.startswith("registry:"):
            name = ref.split(":", 1)[1]
            return self.resolve_registry_name(name)

        if ":" in ref:
            return resolve_import_ref(ref)

        return self.resolve_registry_name(ref)

    def resolve_registry_name(self, name: str) -> Callable[..., Any]:
        name = name.strip()
        callback = self._callbacks.get(name)
        if callback is None:
            raise KeyError(f"no callback registered for '{name}'")
        return callback


def resolve_import_ref(ref: str) -> Callable[..., Any]:
    """Resolve import-style refs such as `my_module:my_callable`."""
    if ":" not in ref:
        raise ValueError(
            f"invalid import ref '{ref}'. expected format: module:function"
        )

    module_name, attr_name = ref.split(":", 1)
    if not module_name or not attr_name:
        raise ValueError(
            f"invalid import ref '{ref}'. expected format: module:function"
        )

    module = import_module(module_name)
    callback = getattr(module, attr_name, None)
    if callback is None:
        raise AttributeError(f"'{module_name}' does not define '{attr_name}'")
    if not callable(callback):
        raise TypeError(f"'{module_name}:{attr_name}' is not callable")

    return callback
