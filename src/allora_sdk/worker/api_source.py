"""API-backed data sources for worker roles.

Provides factory functions that create run callables (compatible with
AlloraWorker.inferer, .forecaster, and .reputer) from HTTP API endpoints.

Architecture:
    APISourceConfig  -- HTTP endpoint settings (frozen dataclass)
    make_api_run_fn  -- generic factory: fetch + ResponseAdapter -> callable
    make_api_*_fn    -- role-specific convenience wrappers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

import aiohttp

T = TypeVar("T")
ResponseAdapter = Callable[[Any], T]


@dataclass(frozen=True)
class APISourceConfig:
    """HTTP endpoint configuration for API-backed worker data sources.

    The ``url`` and ``payload_template`` values support ``{nonce}`` placeholder
    substitution at call time (e.g. ``http://model:8000/predict?block={nonce}``).

    Args:
        url: API endpoint URL. Supports ``{nonce}`` placeholder.
        method: HTTP method (``GET`` or ``POST``).
        response_field: JSON key to extract from the response.
        headers: Extra HTTP headers sent with every request.
        timeout_seconds: Per-request timeout.
        payload_template: POST body template. Values may contain ``{nonce}``.
    """

    url: str
    method: str = "GET"
    response_field: str = "value"
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 10.0
    payload_template: dict[str, str] | None = None


async def _fetch_api_response(config: APISourceConfig, nonce: int) -> Any:
    """Perform the HTTP call and return parsed JSON."""
    url = config.url.format(nonce=nonce)
    timeout = aiohttp.ClientTimeout(total=config.timeout_seconds)

    async with aiohttp.ClientSession(headers=config.headers) as session:
        if config.method.upper() == "POST":
            payload = (
                {k: v.format(nonce=nonce) for k, v in config.payload_template.items()}
                if config.payload_template
                else {"nonce": nonce}
            )
            async with session.post(url, json=payload, timeout=timeout) as resp:
                resp.raise_for_status()
                return await resp.json()
        else:
            async with session.get(url, timeout=timeout) as resp:
                resp.raise_for_status()
                return await resp.json()


def make_api_run_fn(
    config: APISourceConfig,
    parse_response: ResponseAdapter[T],
) -> Callable[[int], Awaitable[T]]:
    """Generic factory: HTTP fetch + response adapter -> run callable.

    Separates the HTTP concern (APISourceConfig) from the response-parsing
    concern (ResponseAdapter), so either can change independently. Use the
    role-specific convenience wrappers for common cases.

    Args:
        config: HTTP endpoint settings.
        parse_response: Transforms the raw JSON response into the role's expected type.

    Returns:
        An async callable ``(nonce: int) -> T`` compatible with any worker role.
    """

    async def run(nonce: int) -> T:
        data = await _fetch_api_response(config, nonce)
        return parse_response(data)

    return run


def _parse_scalar(data: Any, response_field: str) -> str:
    """Extract a scalar string from a JSON response."""
    if isinstance(data, (int, float, str)):
        return str(data)
    if isinstance(data, dict) and response_field in data:
        return str(data[response_field])
    raise ValueError(
        f"Expected a scalar or object with '{response_field}' key, "
        f"got: {str(data)[:200]}"
    )


def _parse_forecasts(data: Any, response_field: str) -> dict[str, float]:
    """Extract an ``{address: value}`` dict from a JSON response."""
    if isinstance(data, dict) and response_field in data:
        raw = data[response_field]
    elif isinstance(data, dict):
        raw = data
    else:
        raise ValueError(
            f"Expected object with '{response_field}' key, " f"got: {str(data)[:200]}"
        )

    if not isinstance(raw, dict):
        raise ValueError(f"Forecasts must be a dict, got {type(raw).__name__}")

    return {str(addr): float(val) for addr, val in raw.items()}


# ── Role-specific convenience factories ──────────────────────────────────


def make_api_inferer_fn(
    config: APISourceConfig,
    response_field: str = "value",
) -> Callable[[int], Awaitable[str]]:
    """Create an inferer run function that fetches predictions from an HTTP API.

    Args:
        config: API endpoint configuration.
        response_field: JSON key containing the inference value.

    Returns:
        Async callable compatible with ``AlloraWorker.inferer(run=...)``.
    """
    return make_api_run_fn(config, lambda data: _parse_scalar(data, response_field))


def make_api_forecaster_fn(
    config: APISourceConfig,
    response_field: str = "forecasts",
) -> Callable[[int], Awaitable[dict[str, float]]]:
    """Create a forecaster run function that fetches forecasts from an HTTP API.

    Expected API response format::

        {"forecasts": {"allo1abc...": 3500.0, "allo1def...": 3510.5}}

    Args:
        config: API endpoint configuration.
        response_field: JSON key containing the ``{address: value}`` mapping.

    Returns:
        Async callable compatible with ``AlloraWorker.forecaster(run=...)``.
    """
    return make_api_run_fn(config, lambda data: _parse_forecasts(data, response_field))


def make_api_ground_truth_fn(
    config: APISourceConfig,
    response_field: str = "value",
) -> Callable[[int], Awaitable[str]]:
    """Create a ground truth function that fetches values from an HTTP API.

    Args:
        config: API endpoint configuration.
        response_field: JSON key containing the ground truth value.

    Returns:
        Async callable compatible with ``AlloraWorker.reputer(ground_truth_fn=...)``.
    """
    return make_api_run_fn(config, lambda data: _parse_scalar(data, response_field))
