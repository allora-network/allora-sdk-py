from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from allora_sdk.worker.cli import _run_config, _validate_config


def test_validate_config_reports_exception_type(capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "allora_sdk.worker.cli.WorkerRunnerConfig.from_file",
        side_effect=AttributeError("missing callback"),
    ):
        with pytest.raises(SystemExit, match="1"):
            _validate_config(Path("worker.yaml"))

    captured = capsys.readouterr()
    assert "config validation failed: AttributeError: missing callback" in captured.err


@pytest.mark.asyncio
async def test_run_config_reports_exception_type(capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "allora_sdk.worker.cli.WorkerRunnerConfig.from_file",
        side_effect=TypeError("callback is not callable"),
    ):
        with pytest.raises(SystemExit, match="1"):
            await _run_config(Path("worker.yaml"))

    captured = capsys.readouterr()
    assert "config load failed: TypeError: callback is not callable" in captured.err


def test_validate_config_does_not_catch_unexpected_exceptions() -> None:
    with patch(
        "allora_sdk.worker.cli.WorkerRunnerConfig.from_file",
        side_effect=ZeroDivisionError("unexpected bug"),
    ):
        with pytest.raises(ZeroDivisionError, match="unexpected bug"):
            _validate_config(Path("worker.yaml"))
