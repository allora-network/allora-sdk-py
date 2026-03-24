"""Example forecast model.

Replace the body of run_model with your own forecasting logic.
Return a dictionary mapping inferer addresses to your predicted values.
The nonce is the block height for the current epoch.
"""


def run_model(nonce: int) -> dict[str, float]:
    return {
        "allo1inferer1...": 3500.0,
        "allo1inferer2...": 3510.5,
    }
