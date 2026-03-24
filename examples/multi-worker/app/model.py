"""Example forecast model used by the multi-worker example.

The inferer and reputer in this example use API sidecars, but the
forecaster uses this local Python function.
"""


def run_forecast(nonce: int) -> dict[str, float]:
    return {
        "allo1inferer1...": 3500.0,
        "allo1inferer2...": 3510.5,
    }
