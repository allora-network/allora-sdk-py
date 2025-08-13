import os


def gen_private_key_hex() -> str:
    """Generate a random 32-byte hex string suitable for tests."""
    return os.urandom(32).hex()


