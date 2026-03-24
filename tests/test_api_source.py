"""Unit tests for allora_sdk.worker.api_source."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from allora_sdk.worker.api_source import (
    APISourceConfig,
    _fetch_api_response,
    _parse_forecasts,
    _parse_scalar,
    make_api_forecaster_fn,
    make_api_ground_truth_fn,
    make_api_inferer_fn,
    make_api_run_fn,
)


class TestAPISourceConfig:
    def test_defaults(self):
        cfg = APISourceConfig(url="http://example.com/api")
        assert cfg.method == "GET"
        assert cfg.response_field is None
        assert cfg.headers == {}
        assert cfg.timeout_seconds == 10.0
        assert cfg.payload_template is None

    def test_custom_values(self):
        cfg = APISourceConfig(
            url="http://model:8000/predict",
            method="POST",
            response_field="prediction",
            headers={"Authorization": "Bearer token123"},
            timeout_seconds=30.0,
            payload_template={"block": "{nonce}"},
        )
        assert cfg.url == "http://model:8000/predict"
        assert cfg.method == "POST"
        assert cfg.response_field == "prediction"
        assert cfg.headers["Authorization"] == "Bearer token123"
        assert cfg.timeout_seconds == 30.0
        assert cfg.payload_template == {"block": "{nonce}"}

    def test_frozen(self):
        cfg = APISourceConfig(url="http://example.com")
        with pytest.raises(AttributeError):
            cfg.url = "http://other.com"  # type: ignore[misc]


class TestParseScalar:
    def test_bare_numeric(self):
        assert _parse_scalar(42, "value") == "42"

    def test_bare_string(self):
        assert _parse_scalar("3500.12", "value") == "3500.12"

    def test_bare_float(self):
        assert _parse_scalar(3.14, "value") == "3.14"

    def test_dict_with_key(self):
        assert _parse_scalar({"value": 100.5}, "value") == "100.5"

    def test_dict_custom_key(self):
        assert _parse_scalar({"prediction": "99"}, "prediction") == "99"

    def test_missing_key_raises(self):
        with pytest.raises(ValueError, match="Expected a scalar"):
            _parse_scalar({"other": 1}, "value")

    def test_list_raises(self):
        with pytest.raises(ValueError, match="Expected a scalar"):
            _parse_scalar([1, 2, 3], "value")


class TestParseForecasts:
    def test_flat_dict(self):
        data = {"allo1abc": 100.0, "allo1def": 200.5}
        result = _parse_forecasts(data, "forecasts")
        assert result == {"allo1abc": 100.0, "allo1def": 200.5}

    def test_nested_key(self):
        data = {"forecasts": {"allo1abc": 100, "allo1def": 200}}
        result = _parse_forecasts(data, "forecasts")
        assert result == {"allo1abc": 100.0, "allo1def": 200.0}

    def test_non_dict_inner_raises(self):
        data = {"forecasts": [1, 2]}
        with pytest.raises(ValueError, match="must be a dict"):
            _parse_forecasts(data, "forecasts")

    def test_non_dict_top_raises(self):
        with pytest.raises(ValueError, match="Expected object"):
            _parse_forecasts("not a dict", "forecasts")


@pytest.mark.asyncio
class TestFetchAPIResponse:
    @patch("allora_sdk.worker.api_source.aiohttp.ClientSession")
    async def test_get_request(self, mock_session_cls):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = AsyncMock(return_value={"value": "42"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = lambda *a, **kw: mock_resp
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        cfg = APISourceConfig(url="http://model:8000/predict?block={nonce}")
        result = await _fetch_api_response(cfg, nonce=42)
        assert result == {"value": "42"}

    @patch("allora_sdk.worker.api_source.aiohttp.ClientSession")
    async def test_post_request_with_template(self, mock_session_cls):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = AsyncMock(return_value={"value": "100"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        captured_kwargs: dict = {}

        def fake_post(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_resp

        mock_session = AsyncMock()
        mock_session.post = fake_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        cfg = APISourceConfig(
            url="http://model:8000/predict",
            method="POST",
            payload_template={"block_height": "{nonce}"},
        )
        result = await _fetch_api_response(cfg, nonce=99)
        assert result == {"value": "100"}
        assert captured_kwargs["json"] == {"block_height": "99"}

    @patch("allora_sdk.worker.api_source.aiohttp.ClientSession")
    async def test_post_default_payload(self, mock_session_cls):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = AsyncMock(return_value={"value": "10"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        captured_kwargs: dict = {}

        def fake_post(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_resp

        mock_session = AsyncMock()
        mock_session.post = fake_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        cfg = APISourceConfig(
            url="http://model:8000/predict",
            method="POST",
        )
        result = await _fetch_api_response(cfg, nonce=5)
        assert result == {"value": "10"}
        assert captured_kwargs["json"] == {"nonce": 5}

    async def test_reuses_shared_session_when_provided(self):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = AsyncMock(return_value={"value": "42"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = AsyncMock()
        session.get = lambda *a, **kw: mock_resp

        cfg = APISourceConfig(
            url="http://model:8000/predict?block={nonce}",
            headers={"Authorization": "Bearer token123"},
        )
        result = await _fetch_api_response(cfg, nonce=42, session=session)

        assert result == {"value": "42"}
        session.close.assert_not_awaited()


@pytest.mark.asyncio
class TestMakeApiRunFn:
    @patch("allora_sdk.worker.api_source._fetch_api_response", new_callable=AsyncMock)
    async def test_custom_adapter(self, mock_fetch):
        mock_fetch.return_value = {"custom_key": 42}
        cfg = APISourceConfig(url="http://example.com")
        fn = make_api_run_fn(cfg, lambda data: data["custom_key"] * 2)
        result = await fn(1)
        assert result == 84
        mock_fetch.assert_awaited_once_with(cfg, 1, session=None)

    @patch("allora_sdk.worker.api_source._fetch_api_response", new_callable=AsyncMock)
    async def test_uses_session_factory(self, mock_fetch):
        mock_fetch.return_value = {"custom_key": 42}
        cfg = APISourceConfig(url="http://example.com")
        session = AsyncMock()
        fn = make_api_run_fn(
            cfg,
            lambda data: data["custom_key"] * 2,
            session_factory=lambda: session,
        )
        result = await fn(1)
        assert result == 84
        mock_fetch.assert_awaited_once_with(cfg, 1, session=session)


@pytest.mark.asyncio
class TestMakeApiInfererFn:
    @patch("allora_sdk.worker.api_source._fetch_api_response", new_callable=AsyncMock)
    async def test_default_field(self, mock_fetch):
        mock_fetch.return_value = {"value": "3500.5"}
        cfg = APISourceConfig(url="http://model:8000/inference")
        fn = make_api_inferer_fn(cfg)
        result = await fn(100)
        assert result == "3500.5"

    @patch("allora_sdk.worker.api_source._fetch_api_response", new_callable=AsyncMock)
    async def test_custom_field(self, mock_fetch):
        mock_fetch.return_value = {"prediction": "99.1"}
        cfg = APISourceConfig(url="http://model:8000/inference")
        fn = make_api_inferer_fn(cfg, response_field="prediction")
        result = await fn(100)
        assert result == "99.1"

    @patch("allora_sdk.worker.api_source._fetch_api_response", new_callable=AsyncMock)
    async def test_config_field_used_by_default(self, mock_fetch):
        mock_fetch.return_value = {"prediction": "99.1"}
        cfg = APISourceConfig(
            url="http://model:8000/inference",
            response_field="prediction",
        )
        fn = make_api_inferer_fn(cfg)
        result = await fn(100)
        assert result == "99.1"

    @patch("allora_sdk.worker.api_source._fetch_api_response", new_callable=AsyncMock)
    async def test_bare_numeric_response(self, mock_fetch):
        mock_fetch.return_value = 3500.0
        cfg = APISourceConfig(url="http://model:8000/inference")
        fn = make_api_inferer_fn(cfg)
        result = await fn(100)
        assert result == "3500.0"


@pytest.mark.asyncio
class TestMakeApiForecasterFn:
    @patch("allora_sdk.worker.api_source._fetch_api_response", new_callable=AsyncMock)
    async def test_default_field(self, mock_fetch):
        mock_fetch.return_value = {
            "forecasts": {"allo1abc": 3500.0, "allo1def": 3510.5}
        }
        cfg = APISourceConfig(url="http://model:8000/forecast")
        fn = make_api_forecaster_fn(cfg)
        result = await fn(100)
        assert result == {"allo1abc": 3500.0, "allo1def": 3510.5}

    @patch("allora_sdk.worker.api_source._fetch_api_response", new_callable=AsyncMock)
    async def test_custom_field(self, mock_fetch):
        mock_fetch.return_value = {"predictions": {"allo1x": 1.0}}
        cfg = APISourceConfig(url="http://model:8000/forecast")
        fn = make_api_forecaster_fn(cfg, response_field="predictions")
        result = await fn(100)
        assert result == {"allo1x": 1.0}

    @patch("allora_sdk.worker.api_source._fetch_api_response", new_callable=AsyncMock)
    async def test_config_field_used_by_default(self, mock_fetch):
        mock_fetch.return_value = {"predictions": {"allo1x": 1.0}}
        cfg = APISourceConfig(
            url="http://model:8000/forecast",
            response_field="predictions",
        )
        fn = make_api_forecaster_fn(cfg)
        result = await fn(100)
        assert result == {"allo1x": 1.0}


@pytest.mark.asyncio
class TestMakeApiGroundTruthFn:
    @patch("allora_sdk.worker.api_source._fetch_api_response", new_callable=AsyncMock)
    async def test_default_field(self, mock_fetch):
        mock_fetch.return_value = {"value": "3495.0"}
        cfg = APISourceConfig(url="http://truth-svc:8001/ground-truth")
        fn = make_api_ground_truth_fn(cfg)
        result = await fn(100)
        assert result == "3495.0"

    @patch("allora_sdk.worker.api_source._fetch_api_response", new_callable=AsyncMock)
    async def test_bare_value(self, mock_fetch):
        mock_fetch.return_value = 3495
        cfg = APISourceConfig(url="http://truth-svc:8001/ground-truth")
        fn = make_api_ground_truth_fn(cfg)
        result = await fn(100)
        assert result == "3495"


@pytest.mark.asyncio
class TestMakeApiNonceSubstitution:
    @patch("allora_sdk.worker.api_source.aiohttp.ClientSession")
    async def test_nonce_in_url(self, mock_session_cls):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = AsyncMock(return_value={"value": "1"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        captured_url = None

        def fake_get(url, **kwargs):
            nonlocal captured_url
            captured_url = url
            return mock_resp

        mock_session = AsyncMock()
        mock_session.get = fake_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        cfg = APISourceConfig(url="http://model:8000/predict?block={nonce}")
        fn = make_api_inferer_fn(cfg)
        await fn(42)
        assert captured_url == "http://model:8000/predict?block=42"
