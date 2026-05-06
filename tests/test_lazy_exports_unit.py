import allora_sdk
import allora_sdk.rpc_client as rpc_client


def test_top_level_lazy_exports_are_cached_in_module_globals() -> None:
    """Verify top-level lazy-loaded exports are cached after first lookup."""
    name = "setup_sdk_logging"
    allora_sdk.__dict__.pop(name, None)

    resolved = getattr(allora_sdk, name)

    assert name in allora_sdk.__dict__
    assert allora_sdk.__dict__[name] is resolved
    assert getattr(allora_sdk, name) is resolved


def test_rpc_client_lazy_exports_are_cached_in_module_globals() -> None:
    """Verify rpc_client lazy-loaded exports are cached after first lookup."""
    name = "AlloraNetworkConfig"
    rpc_client.__dict__.pop(name, None)

    resolved = getattr(rpc_client, name)

    assert name in rpc_client.__dict__
    assert rpc_client.__dict__[name] is resolved
    assert getattr(rpc_client, name) is resolved
