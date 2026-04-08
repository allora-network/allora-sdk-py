"""
Allora Platform Bootstrap Script

This script is the runtime entrypoint for model bundles deployed on the Allora platform.
It reads model.yaml, imports the user's prediction function, and runs the AlloraWorker.

Environment Variables (all optional, override model.yaml defaults):
    ALLORA_MNEMONIC: Wallet mnemonic phrase
    ALLORA_PRIVATE_KEY: Wallet private key (alternative to mnemonic)
    ALLORA_API_KEY: API key for testnet faucet
    ALLORA_TOPIC_ID: Topic ID to submit predictions to
    ALLORA_NETWORK: Network preset ("testnet", "mainnet") or custom RPC URL
    ALLORA_FEE_TIER: Transaction fee tier ("ECO", "STANDARD", "PRIORITY")
    ALLORA_POLLING_INTERVAL: Polling interval in seconds
    BUNDLE_ROOT: Path to bundle directory (default: /workspace/bundle)
"""

import asyncio
import importlib
import inspect
import logging
import os
import sys
import yaml
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from allora_sdk import AlloraNetworkConfig, AlloraWalletConfig, AlloraWorker
from allora_sdk.rpc_client.tx_manager import FeeTier

logger = logging.getLogger("allora_platform")


class BootstrapError(Exception):
    """Raised when bootstrap fails."""
    pass


def load_model_config(bundle_root: Path) -> Dict[str, Any]:
    """
    Load and parse model.yaml from bundle.

    Args:
        bundle_root: Path to bundle directory

    Returns:
        Parsed model.yaml content

    Raises:
        BootstrapError: If model.yaml is missing or invalid
    """
    config_path = bundle_root / "model.yaml"

    if not config_path.exists():
        raise BootstrapError(f"model.yaml not found at {config_path}")

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise BootstrapError(f"Failed to parse model.yaml: {e}")

    if not config:
        raise BootstrapError("model.yaml is empty")

    # Validate required fields
    required_fields = ["schema_version", "name", "version", "python", "entrypoint", "environment"]
    for field in required_fields:
        if field not in config:
            raise BootstrapError(f"Missing required field in model.yaml: {field}")

    return config


def setup_python_path(bundle_root: Path, workdir: str = "."):
    """
    Configure Python path and working directory.

    Args:
        bundle_root: Path to bundle directory
        workdir: Working directory relative to bundle root
    """
    # Add bundle root to Python path
    bundle_root_str = str(bundle_root.resolve())
    if bundle_root_str not in sys.path:
        sys.path.insert(0, bundle_root_str)
        logger.info(f"Added to PYTHONPATH: {bundle_root_str}")

    # Change to working directory
    workdir_path = bundle_root / workdir
    if workdir_path.exists():
        os.chdir(workdir_path)
        logger.info(f"Changed working directory to: {workdir_path}")
    else:
        logger.warning(f"Working directory not found: {workdir_path}, using bundle root")
        os.chdir(bundle_root)


def load_user_function(module_name: str, function_name: str) -> Callable:
    """
    Import and return the user's prediction function.

    Args:
        module_name: Importable module path (e.g., "src.my_pkg.runner")
        function_name: Function name within the module (e.g., "run")

    Returns:
        The user's prediction function

    Raises:
        BootstrapError: If import fails or function not found
    """
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise BootstrapError(
            f"Failed to import module '{module_name}': {e}\n"
            f"Make sure your module is in the bundle and PYTHONPATH is correct."
        )

    if not hasattr(module, function_name):
        raise BootstrapError(
            f"Function '{function_name}' not found in module '{module_name}'\n"
            f"Available attributes: {dir(module)}"
        )

    user_function = getattr(module, function_name)

    if not callable(user_function):
        raise BootstrapError(f"'{function_name}' in '{module_name}' is not callable")

    logger.info(f"Loaded user function: {module_name}.{function_name}")

    # Check signature (should accept nonce: int)
    sig = inspect.signature(user_function)
    params = list(sig.parameters.keys())

    if len(params) == 0:
        logger.warning(
            f"User function has no parameters. Expected signature: (nonce: int) -> float\n"
            f"The function will be called without arguments."
        )
    elif len(params) == 1:
        logger.info(f"User function accepts 1 parameter: {params[0]}")
    else:
        logger.warning(
            f"User function accepts {len(params)} parameters: {params}\n"
            f"Expected signature: (nonce: int) -> float\n"
            f"Only the first parameter (nonce) will be passed."
        )

    return user_function


def get_wallet_config() -> AlloraWalletConfig:
    """
    Get wallet configuration from environment variables.

    Returns:
        AlloraWalletConfig instance

    Raises:
        BootstrapError: If neither mnemonic nor private key is provided
    """
    mnemonic = os.environ.get("ALLORA_MNEMONIC")
    private_key = os.environ.get("ALLORA_PRIVATE_KEY")

    if mnemonic:
        logger.info("Using wallet from ALLORA_MNEMONIC")
        return AlloraWalletConfig(mnemonic=mnemonic)
    elif private_key:
        logger.info("Using wallet from ALLORA_PRIVATE_KEY")
        return AlloraWalletConfig(private_key=private_key)
    else:
        raise BootstrapError(
            "Wallet credentials not found. "
            "Set either ALLORA_MNEMONIC or ALLORA_PRIVATE_KEY environment variable."
        )


def get_network_config(config: Dict[str, Any]) -> AlloraNetworkConfig:
    """
    Get network configuration from environment or model.yaml.

    Args:
        config: Parsed model.yaml content

    Returns:
        AlloraNetworkConfig instance
    """
    network = os.environ.get("ALLORA_NETWORK")

    # If not in env, try model.yaml
    if not network and "allora" in config and "network" in config["allora"]:
        network = config["allora"]["network"]

    # Default to testnet
    if not network:
        network = "testnet"

    logger.info(f"Using network: {network}")

    if network == "testnet":
        return AlloraNetworkConfig.testnet()
    elif network == "mainnet":
        return AlloraNetworkConfig.mainnet()
    else:
        # Assume it's a custom RPC URL
        logger.info(f"Using custom RPC URL: {network}")
        return AlloraNetworkConfig(
            rpc_url=network,
            grpc_url=network.replace("https://", "").replace("http://", ""),
        )


def get_fee_tier(config: Dict[str, Any]) -> FeeTier:
    """
    Get fee tier from environment or model.yaml.

    Args:
        config: Parsed model.yaml content

    Returns:
        FeeTier enum value
    """
    fee_tier_str = os.environ.get("ALLORA_FEE_TIER")

    # If not in env, try model.yaml
    if not fee_tier_str and "allora" in config and "fee_tier" in config["allora"]:
        fee_tier_str = config["allora"]["fee_tier"]

    # Default to STANDARD
    if not fee_tier_str:
        fee_tier_str = "STANDARD"

    logger.info(f"Using fee tier: {fee_tier_str}")

    try:
        return FeeTier[fee_tier_str.upper()]
    except KeyError:
        logger.warning(f"Invalid fee tier '{fee_tier_str}', using STANDARD")
        return FeeTier.STANDARD


def get_topic_id(config: Dict[str, Any]) -> int:
    """
    Get topic ID from environment or model.yaml.

    Args:
        config: Parsed model.yaml content

    Returns:
        Topic ID

    Raises:
        BootstrapError: If topic_id is not provided or invalid
    """
    topic_id_str = os.environ.get("ALLORA_TOPIC_ID")

    # If not in env, try model.yaml
    if not topic_id_str and "allora" in config and "topic_id" in config["allora"]:
        topic_id_str = str(config["allora"]["topic_id"])

    if not topic_id_str:
        raise BootstrapError(
            "Topic ID not provided. "
            "Set ALLORA_TOPIC_ID environment variable or specify in model.yaml"
        )

    try:
        topic_id = int(topic_id_str)
    except ValueError:
        raise BootstrapError(f"Invalid topic ID: {topic_id_str}")

    logger.info(f"Using topic ID: {topic_id}")
    return topic_id


def get_polling_interval(config: Dict[str, Any]) -> int:
    """
    Get polling interval from environment or model.yaml.

    Args:
        config: Parsed model.yaml content

    Returns:
        Polling interval in seconds
    """
    interval_str = os.environ.get("ALLORA_POLLING_INTERVAL")

    # If not in env, try model.yaml
    if not interval_str and "allora" in config and "polling_interval" in config["allora"]:
        interval_str = str(config["allora"]["polling_interval"])

    # Default to 120 seconds
    if not interval_str:
        return 120

    try:
        interval = int(interval_str)
    except ValueError:
        logger.warning(f"Invalid polling interval '{interval_str}', using 120")
        return 120

    logger.info(f"Using polling interval: {interval} seconds")
    return interval


async def main():
    """Main entry point for the platform bootstrap."""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("=== Allora Platform Bootstrap Starting ===")

    # Get bundle root
    bundle_root_str = os.environ.get("BUNDLE_ROOT", "/workspace/bundle")
    bundle_root = Path(bundle_root_str)

    if not bundle_root.exists():
        raise BootstrapError(f"Bundle root not found: {bundle_root}")

    logger.info(f"Bundle root: {bundle_root}")

    try:
        # Load model configuration
        config = load_model_config(bundle_root)
        logger.info(f"Loaded model: {config['name']} v{config['version']}")
        logger.info(f"Python version required: {config['python']['version']}")

        # Setup Python path and working directory
        entrypoint = config["entrypoint"]
        workdir = entrypoint.get("workdir", ".")
        setup_python_path(bundle_root, workdir)

        # Load user's prediction function
        module_name = entrypoint["module"]
        function_name = entrypoint["function"]
        user_function = load_user_function(module_name, function_name)

        # Get configuration
        wallet = get_wallet_config()
        network = get_network_config(config)
        fee_tier = get_fee_tier(config)
        topic_id = get_topic_id(config)
        polling_interval = get_polling_interval(config)
        api_key = os.environ.get("ALLORA_API_KEY")

        # Create worker
        logger.info("Creating AlloraWorker...")
        worker = AlloraWorker.inferer(
            run=user_function,
            wallet=wallet,
            network=network,
            api_key=api_key,
            topic_id=topic_id,
            fee_tier=fee_tier,
            polling_interval=polling_interval,
            debug=os.environ.get("DEBUG", "").lower() in ("true", "1", "yes"),
        )

        logger.info("=== Starting Worker Loop ===")

        # Run worker and handle results
        async for result in worker.run():
            if isinstance(result, Exception):
                logger.error(f"Worker error: {result}")
            else:
                logger.info(f"Submission result: {result}")

    except BootstrapError as e:
        logger.error(f"Bootstrap failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
