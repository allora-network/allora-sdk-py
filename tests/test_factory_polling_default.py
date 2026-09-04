"""The documented entry points must leave polling derivation enabled.

A factory that passes its own default down looks harmless but disables the
window-aware derivation for every caller who does not set the argument -- which
is the common case, and the one the derivation exists for.
"""

import inspect

import pytest

from allora_sdk.worker.worker import AlloraWorker


@pytest.mark.parametrize("factory", ["inferer", "reputer", "forecaster"])
def test_factory_leaves_polling_interval_unset(factory):
    sig = inspect.signature(getattr(AlloraWorker, factory))
    default = sig.parameters["polling_interval"].default
    assert default is None, (
        f"AlloraWorker.{factory} defaults polling_interval to {default!r}; "
        "an explicit value suppresses derivation from the topic window"
    )


def test_constructor_leaves_polling_interval_unset():
    sig = inspect.signature(AlloraWorker.__init__)
    assert sig.parameters["polling_interval"].default is None
