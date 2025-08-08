"""
Allora Query Interface

This module provides query functionality for retrieving data from
the Allora blockchain including account information, model data,
inference requests, and network parameters.
"""

import logging
from typing import Dict, Any, Optional, List
import aiohttp
import json

from cosmpy.aerial.client import LedgerClient
from cosmpy.crypto.address import Address

logger = logging.getLogger(__name__)


class AlloraQueries:
    """
    Query interface for Allora blockchain data retrieval.
    
    Provides high-level methods for querying various blockchain modules
    including bank, staking, governance, and Allora-specific modules.
    """
    
    def __init__(self, client):
        """Initialize with Allora client."""
        self.client = client
        self.ledger_client: LedgerClient = client.ledger_client
        self.rest_endpoint = client.config.rest_endpoint
    
    # Account and balance queries
    
    
    def get_validator(self, validator_address: str) -> Optional[Dict[str, Any]]:
        """
        Get specific validator information.
        
        Args:
            validator_address: Validator operator address
            
        Returns:
            Validator information or None if not found
        """
        try:
            validator = self.ledger_client.query_validator(validator_address)
            return {
                "operator_address": validator.operator_address,
                "consensus_pubkey": validator.consensus_pubkey,
                "jailed": validator.jailed,
                "status": validator.status,
                "tokens": validator.tokens,
                "delegator_shares": validator.delegator_shares,
                "description": {
                    "moniker": validator.description.moniker,
                    "identity": validator.description.identity,
                    "website": validator.description.website,
                    "security_contact": validator.description.security_contact,
                    "details": validator.description.details
                }
            }
        except Exception as e:
            logger.error(f"Failed to query validator {validator_address}: {e}")
            return None
    
    def get_delegations(self, delegator_address: str) -> List[Dict[str, Any]]:
        """
        Get delegations for a delegator.
        
        Args:
            delegator_address: Delegator address
            
        Returns:
            List of delegation information
        """
        try:
            delegations = self.ledger_client.query_delegations(delegator_address)
            return [
                {
                    "delegator_address": delegation.delegator_address,
                    "validator_address": delegation.validator_address,
                    "shares": delegation.shares
                }
                for delegation in delegations
            ]
        except Exception as e:
            logger.error(f"Failed to query delegations for {delegator_address}: {e}")
            return []
    
    def get_staking_pool(self) -> Dict[str, Any]:
        """Get staking pool information."""
        try:
            pool = self.ledger_client.query_staking_pool()
            return {
                "not_bonded_tokens": pool.not_bonded_tokens,
                "bonded_tokens": pool.bonded_tokens
            }
        except Exception as e:
            logger.error(f"Failed to query staking pool: {e}")
            return {"error": str(e)}
    
    # Block and transaction queries
    
    def get_latest_block(self) -> Dict[str, Any]:
        """Get latest block information."""
        try:
            block = self.ledger_client.query_latest_block()
            return {
                "height": block.height,
                "time": block.time.isoformat() if block.time else None,
                "chain_id": block.chain_id,
                "hash": block.hash.hex() if block.hash else None,
                "num_txs": len(block.txs) if block.txs else 0
            }
        except Exception as e:
            logger.error(f"Failed to query latest block: {e}")
            return {"error": str(e)}
    
    def get_block_by_height(self, height: int) -> Dict[str, Any]:
        """
        Get block by height.
        
        Args:
            height: Block height
            
        Returns:
            Block information
        """
        try:
            block = self.ledger_client.query_block_by_height(height)
            return {
                "height": block.height,
                "time": block.time.isoformat() if block.time else None,
                "chain_id": block.chain_id,
                "hash": block.hash.hex() if block.hash else None,
                "num_txs": len(block.txs) if block.txs else 0,
                "proposer_address": block.proposer_address
            }
        except Exception as e:
            logger.error(f"Failed to query block at height {height}: {e}")
            return {"error": str(e)}
    
    def get_transaction(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        """
        Get transaction by hash.
        
        Args:
            tx_hash: Transaction hash
            
        Returns:
            Transaction information or None if not found
        """
        try:
            tx = self.ledger_client.query_tx(tx_hash)
            return {
                "txhash": tx.txhash,
                "height": tx.height,
                "code": tx.code,
                "raw_log": tx.raw_log,
                "gas_wanted": tx.gas_wanted,
                "gas_used": tx.gas_used,
                "timestamp": tx.timestamp
            }
        except Exception as e:
            logger.error(f"Failed to query transaction {tx_hash}: {e}")
            return None
    
    # Network info queries
    
    def get_node_info(self) -> Dict[str, Any]:
        """Get node information."""
        try:
            node_info = self.ledger_client.query_node_info()
            return {
                "default_node_id": node_info.default_node_id,
                "protocol_version": {
                    "p2p": node_info.protocol_version.p2p,
                    "block": node_info.protocol_version.block,
                    "app": node_info.protocol_version.app
                },
                "network": node_info.network,
                "version": node_info.version,
                "channels": node_info.channels,
                "moniker": node_info.moniker
            }
        except Exception as e:
            logger.error(f"Failed to query node info: {e}")
            return {"error": str(e)}
    
    def get_syncing_status(self) -> Dict[str, Any]:
        """Get node syncing status."""
        try:
            # This would use cosmpy's syncing query when available
            return {"syncing": False}  # Placeholder
        except Exception as e:
            logger.error(f"Failed to query syncing status: {e}")
            return {"error": str(e)}
    
    # Allora-specific queries (placeholders for when Allora protobuf definitions are available)
    
    async def get_inference_requests(
        self,
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Get inference requests from the network.
        
        Args:
            limit: Maximum number of requests to return
            offset: Offset for pagination
            
        Returns:
            List of inference requests
            
        Note: This is a placeholder implementation.
        """
        try:
            # This would use actual Allora query endpoints
            async with aiohttp.ClientSession() as session:
                url = f"{self.rest_endpoint}/allora/inference/v1/requests"
                params = {"limit": limit, "offset": offset}
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        return {"error": f"HTTP {response.status}"}
        except Exception as e:
            logger.error(f"Failed to query inference requests: {e}")
            return {"error": str(e)}
    
    async def get_network_parameters(self) -> Dict[str, Any]:
        """
        Get network parameters for various modules.
        
        Returns:
            Network parameters
        """
        try:
            # This would query various module parameters
            params = {}
            
            # Bank module parameters
            try:
                bank_params = self.ledger_client.query_bank_params()
                params["bank"] = {
                    "send_enabled": bank_params.send_enabled,
                    "default_send_enabled": bank_params.default_send_enabled
                }
            except Exception as e:
                logger.warning(f"Failed to query bank params: {e}")
            
            # Staking module parameters
            try:
                staking_params = self.ledger_client.query_staking_params()
                params["staking"] = {
                    "unbonding_time": staking_params.unbonding_time,
                    "max_validators": staking_params.max_validators,
                    "max_entries": staking_params.max_entries,
                    "historical_entries": staking_params.historical_entries,
                    "bond_denom": staking_params.bond_denom,
                    "min_commission_rate": staking_params.min_commission_rate
                }
            except Exception as e:
                logger.warning(f"Failed to query staking params: {e}")
            
            return params
        except Exception as e:
            logger.error(f"Failed to query network parameters: {e}")
            return {"error": str(e)}
    
    # Utility methods
    
    async def query_custom_endpoint(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Query a custom REST endpoint.
        
        Args:
            endpoint: API endpoint path
            params: Query parameters
            
        Returns:
            API response
        """
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.rest_endpoint}{endpoint}"
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        return {
                            "error": f"HTTP {response.status}",
                            "message": await response.text()
                        }
        except Exception as e:
            logger.error(f"Failed to query custom endpoint {endpoint}: {e}")
            return {"error": str(e)}
    
    def is_address_valid(self, address: str) -> bool:
        """
        Validate if an address is properly formatted.
        
        Args:
            address: Address to validate
            
        Returns:
            True if address is valid
        """
        try:
            # Basic validation - would be more sophisticated in practice
            if not address:
                return False
            
            # Cosmos addresses typically start with chain-specific prefix
            # and are bech32 encoded
            return len(address) > 10 and len(address) < 100
            
        except Exception:
            return False