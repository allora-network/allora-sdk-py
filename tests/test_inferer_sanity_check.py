"""
Tests for inferer sanity-check throttle behavior and configuration.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from allora_sdk.rpc_client.tx_manager import FeeTier
from allora_sdk.worker.inferer import Inferer, SanityCheckConfig


def _make_mock_client() -> MagicMock:
    """Create a mock AlloraRPCClient with emissions.query stub."""
    client = MagicMock()
    client.emissions = MagicMock()
    client.emissions.query = MagicMock()
    client.emissions.tx = MagicMock()
    return client


def _make_inferer_values_response(*values: float):
    """Build mock response with inferer values (need >= 3 for sanity check)."""
    inferer_values = [MagicMock(value=str(v)) for v in values]
    network_inferences = MagicMock()
    network_inferences.inferer_values = inferer_values
    response = MagicMock()
    response.network_inferences = network_inferences
    return response


def _make_inferer(
    wallet: MagicMock,
    client: MagicMock,
    topic_id: int = 69,
    sanity_check: SanityCheckConfig | None = None,
) -> Inferer:
    return Inferer(
        wallet=wallet,
        client=client,
        topic_id=topic_id,
        run=lambda n: 100.0,
        fee_tier=FeeTier.STANDARD,
        autostake=None,
        sanity_check=sanity_check,
    )


# ---------------------------------------------------------------------------
# SanityCheckConfig
# ---------------------------------------------------------------------------


class TestSanityCheckConfig:
    def test_default_enabled(self) -> None:
        cfg = SanityCheckConfig()
        assert cfg.enabled is True
        assert cfg.throttle_interval_seconds == 60.0

    def test_explicit_disabled(self) -> None:
        cfg = SanityCheckConfig(enabled=False)
        assert cfg.enabled is False

    def test_custom_interval(self) -> None:
        cfg = SanityCheckConfig(throttle_interval_seconds=30.5)
        assert cfg.throttle_interval_seconds == 30.5

    def test_negative_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match="throttle_interval_seconds must be >= 0"):
            SanityCheckConfig(throttle_interval_seconds=-1.0)


# ---------------------------------------------------------------------------
# Sanity check disabled - no RPC
# ---------------------------------------------------------------------------


class TestSanityCheckDisabled:
    @pytest.mark.asyncio
    async def test_disabled_skips_rpc(self):
        """When sanity_check.enabled=False, get_latest_network_inferences is never called."""
        wallet = MagicMock()
        client = _make_mock_client()
        client.emissions.tx.insert_worker_payload = AsyncMock(return_value=MagicMock(wait=AsyncMock()))
        inferer = _make_inferer(wallet, client, sanity_check=SanityCheckConfig(enabled=False))

        await inferer.submit(nonce=1, account_seq=0)
        await inferer.submit(nonce=2, account_seq=1)

        client.emissions.query.get_latest_network_inferences.assert_not_called()


# ---------------------------------------------------------------------------
# Throttle - cache used within interval
# ---------------------------------------------------------------------------


class TestSanityCheckThrottle:
    @pytest.mark.asyncio
    async def test_first_submit_triggers_rpc(self):
        """First submission with sanity check enabled triggers one RPC."""
        wallet = MagicMock()
        client = _make_mock_client()
        client.emissions.query.get_latest_network_inferences = AsyncMock(
            return_value=_make_inferer_values_response(100.0, 101.0, 102.0)
        )
        pending = MagicMock()
        pending.wait = AsyncMock(return_value=MagicMock())
        client.emissions.tx.insert_worker_payload = AsyncMock(return_value=pending)

        inferer = _make_inferer(
            wallet,
            client,
            sanity_check=SanityCheckConfig(enabled=True, throttle_interval_seconds=10.0),
        )

        await inferer.submit(nonce=1, account_seq=0)

        assert client.emissions.query.get_latest_network_inferences.call_count == 1

    @pytest.mark.asyncio
    async def test_second_submit_within_interval_uses_cache(self):
        """Second submission within throttle interval does NOT trigger new RPC."""
        wallet = MagicMock()
        client = _make_mock_client()
        client.emissions.query.get_latest_network_inferences = AsyncMock(
            return_value=_make_inferer_values_response(100.0, 101.0, 102.0)
        )
        pending = MagicMock()
        pending.wait = AsyncMock(return_value=MagicMock())
        client.emissions.tx.insert_worker_payload = AsyncMock(return_value=pending)

        inferer = _make_inferer(
            wallet,
            client,
            sanity_check=SanityCheckConfig(enabled=True, throttle_interval_seconds=10.0),
        )

        with patch("allora_sdk.worker.inferer.time") as mock_time:
            mock_time.monotonic.return_value = 1000.0
            await inferer.submit(nonce=1, account_seq=0)

            mock_time.monotonic.return_value = 1005.0  # 5s later, still within 10s
            await inferer.submit(nonce=2, account_seq=1)

        assert client.emissions.query.get_latest_network_inferences.call_count == 1

    @pytest.mark.asyncio
    async def test_submit_after_interval_expires_triggers_new_rpc(self):
        """When throttle interval expires, a new RPC is made."""
        wallet = MagicMock()
        client = _make_mock_client()
        client.emissions.query.get_latest_network_inferences = AsyncMock(
            return_value=_make_inferer_values_response(100.0, 101.0, 102.0)
        )
        pending = MagicMock()
        pending.wait = AsyncMock(return_value=MagicMock())
        client.emissions.tx.insert_worker_payload = AsyncMock(return_value=pending)

        inferer = _make_inferer(
            wallet,
            client,
            sanity_check=SanityCheckConfig(enabled=True, throttle_interval_seconds=10.0),
        )

        with patch("allora_sdk.worker.inferer.time") as mock_time:
            mock_time.monotonic.return_value = 1000.0
            await inferer.submit(nonce=1, account_seq=0)

            mock_time.monotonic.return_value = 1011.0  # 11s later, interval expired
            await inferer.submit(nonce=2, account_seq=1)

        assert client.emissions.query.get_latest_network_inferences.call_count == 2

    @pytest.mark.asyncio
    async def test_cache_applies_when_fewer_than_three_values(self) -> None:
        """Topics with only 1-2 inferers still benefit from throttle cache."""
        wallet = MagicMock()
        client = _make_mock_client()
        client.emissions.query.get_latest_network_inferences = AsyncMock(
            return_value=_make_inferer_values_response(100.0, 101.0)
        )
        pending = MagicMock()
        pending.wait = AsyncMock(return_value=MagicMock())
        client.emissions.tx.insert_worker_payload = AsyncMock(return_value=pending)

        inferer = _make_inferer(
            wallet,
            client,
            sanity_check=SanityCheckConfig(enabled=True, throttle_interval_seconds=10.0),
        )

        with patch("allora_sdk.worker.inferer.time") as mock_time:
            mock_time.monotonic.return_value = 2000.0
            await inferer.submit(nonce=1, account_seq=0)

            mock_time.monotonic.return_value = 2005.0
            await inferer.submit(nonce=2, account_seq=1)

        assert client.emissions.query.get_latest_network_inferences.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_applies_when_response_has_no_inferences(self) -> None:
        """Empty/None inference responses are cached to avoid repeated RPC spam."""
        wallet = MagicMock()
        client = _make_mock_client()
        response = MagicMock()
        response.network_inferences = None
        client.emissions.query.get_latest_network_inferences = AsyncMock(return_value=response)
        pending = MagicMock()
        pending.wait = AsyncMock(return_value=MagicMock())
        client.emissions.tx.insert_worker_payload = AsyncMock(return_value=pending)

        inferer = _make_inferer(
            wallet,
            client,
            sanity_check=SanityCheckConfig(enabled=True, throttle_interval_seconds=10.0),
        )

        with patch("allora_sdk.worker.inferer.time") as mock_time:
            mock_time.monotonic.return_value = 3000.0
            await inferer.submit(nonce=1, account_seq=0)

            mock_time.monotonic.return_value = 3005.0
            await inferer.submit(nonce=2, account_seq=1)

        assert client.emissions.query.get_latest_network_inferences.call_count == 1
