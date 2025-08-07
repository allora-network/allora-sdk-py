"""
Allora Blockchain Communication Subpackage

This subpackage provides Python-based Cosmos SDK RPC client functionality
for interacting with the Allora blockchain network.

Main components:
- client: Base Allora blockchain client wrapper
- events: WebSocket event subscription handler
- transactions: Transaction builders and signers
- queries: Query helpers for Allora-specific modules
- config: Network configurations and endpoints
- utils: Helper utilities
"""

from .client import ProtobufClient
from .config import AlloraNetworkConfig
from .events import AlloraWebsocketSubscriber
from .queries import AlloraQueries
from .transactions import AlloraTransactions

__version__ = "0.1.0"
__all__ = [
    "ProtobufClient",
    "AlloraNetworkConfig", 
    "AlloraWebsocketSubscriber",
    "AlloraQueries",
    "AlloraTransactions",
]