"""Example ground truth provider.

Replace the body of get_ground_truth with your own logic to fetch
the actual outcome (e.g. real ETH price at the time of the prediction).
The nonce is the block height for the current epoch.
"""


def get_ground_truth(nonce: int) -> float:
    return 3519.88
