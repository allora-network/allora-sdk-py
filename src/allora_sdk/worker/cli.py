"""CLI entrypoint for config-driven worker orchestration."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from allora_sdk.logging_config import setup_sdk_logging
from allora_sdk.worker.runner_config import WorkerRunnerConfig
from allora_sdk.worker.worker_manager import WorkerManager


def main() -> None:
    """Execute the worker runner CLI."""
    parser = argparse.ArgumentParser(prog="allora-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="Validate a worker config file"
    )
    validate_parser.add_argument(
        "--config", required=True, help="Path to YAML/JSON worker config"
    )
    validate_parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )

    run_parser = subparsers.add_parser("run", help="Run workers from a config file")
    run_parser.add_argument(
        "--config", required=True, help="Path to YAML/JSON worker config"
    )
    run_parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    setup_sdk_logging(debug=args.debug)

    if args.command == "validate":
        _validate_config(Path(args.config))
        return

    if args.command == "run":
        asyncio.run(_run_config(Path(args.config)))
        return


def _validate_config(path: Path) -> None:
    try:
        config = WorkerRunnerConfig.from_file(path)
    except (FileNotFoundError, ValueError, RuntimeError) as err:
        print(f"config validation failed: {err}", file=sys.stderr)
        sys.exit(1)

    manager = WorkerManager(config=config)
    for line in manager.startup_summary():
        print(line)
    print("config validation succeeded")


async def _run_config(path: Path) -> None:
    try:
        config = WorkerRunnerConfig.from_file(path)
    except (FileNotFoundError, ValueError, RuntimeError) as err:
        print(f"config load failed: {err}", file=sys.stderr)
        sys.exit(1)

    manager = WorkerManager(config=config)

    print("starting worker manager with configuration:")
    for line in manager.startup_summary():
        print(line)

    await manager.run()
