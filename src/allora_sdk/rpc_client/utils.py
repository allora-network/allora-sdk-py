"""
Allora Blockchain Utilities

This module provides utility functions for working with Allora blockchain
data including address validation, unit conversion, and data formatting.
"""

import hashlib
import json
import logging
from typing import Dict, Any, Optional, Union
from decimal import Decimal
import bech32

logger = logging.getLogger("allora_sdk")


class AlloraUtils:
    """
    Utility functions for Allora blockchain operations.
    
    Provides helper methods for address validation, unit conversion,
    data formatting, and other common blockchain operations.
    """
    
    def __init__(self, client):
        """Initialize with Allora client."""
        self.client = client
        self.chain_prefix = self._get_chain_prefix()
    
    def _get_chain_prefix(self) -> str:
        """Get the chain prefix for addresses."""
        # Extract prefix from chain ID or use default
        chain_id = self.client.config.chain_id
        if "testnet" in chain_id:
            return "allo"  # Testnet prefix
        elif "mainnet" in chain_id:
            return "allo"  # Mainnet prefix
        else:
            return "allo"  # Default prefix
    
    # Address utilities
    
    def is_valid_address(self, address: str) -> bool:
        """
        Validate if an address is properly formatted.
        
        Args:
            address: Address to validate
            
        Returns:
            True if address is valid
        """
        try:
            if not address or not isinstance(address, str):
                return False
            
            # Check if it starts with expected prefix
            if not address.startswith(self.chain_prefix):
                return False
            
            # Validate bech32 encoding
            hrp, data = bech32.bech32_decode(address)
            if hrp != self.chain_prefix or data is None:
                return False
            
            # Convert from 5-bit to 8-bit and check length
            decoded = bech32.convertbits(data, 5, 8, False)
            if decoded is None or len(decoded) != 20:
                return False
            
            return True
            
        except Exception as e:
            logger.debug(f"Address validation failed for {address}: {e}")
            return False
    
    def is_valid_validator_address(self, address: str) -> bool:
        """
        Validate if a validator address is properly formatted.
        
        Args:
            address: Validator address to validate
            
        Returns:
            True if validator address is valid
        """
        try:
            if not address or not isinstance(address, str):
                return False
            
            # Validator addresses typically have 'valoper' suffix
            expected_prefix = f"{self.chain_prefix}valoper"
            return address.startswith(expected_prefix) and self.is_valid_address(address.replace("valoper", ""))
            
        except Exception as e:
            logger.debug(f"Validator address validation failed for {address}: {e}")
            return False
    
    def get_address_type(self, address: str) -> Optional[str]:
        """
        Determine the type of address.
        
        Args:
            address: Address to analyze
            
        Returns:
            Address type ('account', 'validator', 'consensus', etc.) or None
        """
        if not address:
            return None
        
        prefix = self.chain_prefix
        
        if address.startswith(f"{prefix}valoper"):
            return "validator_operator"
        elif address.startswith(f"{prefix}valcons"):
            return "validator_consensus"
        elif address.startswith(f"{prefix}1"):
            return "account"
        elif address.startswith(prefix):
            return "account"
        else:
            return None
    
    # Unit conversion utilities
    
    def to_base_units(self, amount: Union[str, float, Decimal], decimals: int = 6) -> int:
        """
        Convert human-readable amount to base units.
        
        Args:
            amount: Amount in human-readable format
            decimals: Number of decimal places
            
        Returns:
            Amount in base units
        """
        try:
            decimal_amount = Decimal(str(amount))
            base_amount = decimal_amount * (10 ** decimals)
            return int(base_amount)
        except Exception as e:
            logger.error(f"Failed to convert {amount} to base units: {e}")
            raise ValueError(f"Invalid amount: {amount}")
    
    def from_base_units(self, amount: int, decimals: int = 6) -> Decimal:
        """
        Convert base units to human-readable amount.
        
        Args:
            amount: Amount in base units
            decimals: Number of decimal places
            
        Returns:
            Amount in human-readable format
        """
        try:
            divisor = 10 ** decimals
            return Decimal(amount) / Decimal(divisor)
        except Exception as e:
            logger.error(f"Failed to convert {amount} from base units: {e}")
            raise ValueError(f"Invalid amount: {amount}")
    
    def format_token_amount(
        self,
        amount: int,
        denom: str,
        decimals: int = 6,
        show_denom: bool = True
    ) -> str:
        """
        Format token amount for display.
        
        Args:
            amount: Amount in base units
            denom: Token denomination
            decimals: Number of decimal places
            show_denom: Whether to include denomination in output
            
        Returns:
            Formatted amount string
        """
        try:
            readable_amount = self.from_base_units(amount, decimals)
            
            # Format with appropriate precision
            if readable_amount >= 1:
                formatted = f"{readable_amount:.2f}"
            else:
                formatted = f"{readable_amount:.6f}".rstrip('0').rstrip('.')
            
            if show_denom:
                display_denom = self.get_display_denom(denom)
                return f"{formatted} {display_denom}"
            else:
                return formatted
                
        except Exception as e:
            logger.error(f"Failed to format amount {amount} {denom}: {e}")
            return f"{amount} {denom}"
    
    def get_display_denom(self, denom: str) -> str:
        """
        Get display denomination from base denomination.
        
        Args:
            denom: Base denomination
            
        Returns:
            Display denomination
        """
        display_denoms = {
            "uallo": "ALLO",
            "uatom": "ATOM",
            "ustake": "STAKE"
        }
        return display_denoms.get(denom, denom.upper())
    
    # Data formatting utilities
    
    def format_block_time(self, timestamp: str) -> str:
        """
        Format block timestamp for display.
        
        Args:
            timestamp: ISO timestamp string
            
        Returns:
            Formatted timestamp
        """
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception as e:
            logger.error(f"Failed to format timestamp {timestamp}: {e}")
            return timestamp
    
    def truncate_hash(self, hash_str: str, length: int = 8) -> str:
        """
        Truncate hash for display.
        
        Args:
            hash_str: Full hash string
            length: Number of characters to show from start and end
            
        Returns:
            Truncated hash
        """
        if not hash_str or len(hash_str) <= length * 2:
            return hash_str
        
        return f"{hash_str[:length]}...{hash_str[-length:]}"
    
    def format_gas_info(self, gas_wanted: int, gas_used: int) -> Dict[str, Any]:
        """
        Format gas information for display.
        
        Args:
            gas_wanted: Gas limit
            gas_used: Actual gas used
            
        Returns:
            Formatted gas information
        """
        efficiency = (gas_used / gas_wanted * 100) if gas_wanted > 0 else 0
        
        return {
            "gas_wanted": f"{gas_wanted:,}",
            "gas_used": f"{gas_used:,}",
            "efficiency": f"{efficiency:.1f}%",
            "wasted": f"{max(0, gas_wanted - gas_used):,}"
        }
    
    # Cryptographic utilities
    
    def hash_data(self, data: Union[str, bytes, Dict]) -> str:
        """
        Hash data using SHA-256.
        
        Args:
            data: Data to hash
            
        Returns:
            Hex-encoded hash
        """
        try:
            if isinstance(data, dict):
                data = json.dumps(data, sort_keys=True)
            elif isinstance(data, str):
                data = data.encode('utf-8')
            
            return hashlib.sha256(data).hexdigest()
        except Exception as e:
            logger.error(f"Failed to hash data: {e}")
            raise ValueError(f"Invalid data for hashing: {type(data)}")
    
    def verify_hash(self, data: Union[str, bytes, Dict], expected_hash: str) -> bool:
        """
        Verify data against expected hash.
        
        Args:
            data: Data to verify
            expected_hash: Expected hash value
            
        Returns:
            True if hash matches
        """
        try:
            actual_hash = self.hash_data(data)
            return actual_hash.lower() == expected_hash.lower()
        except Exception as e:
            logger.error(f"Failed to verify hash: {e}")
            return False
    
    # Network utilities
    
    def get_network_info_summary(self) -> Dict[str, Any]:
        """
        Get a summary of network information.
        
        Returns:
            Network information summary
        """
        try:
            info = self.client.get_network_info()
            return {
                "network": self.client.config.chain_id,
                "latest_block": info.get("latest_block", "Unknown"),
                "connected": info.get("connected", False),
                "endpoints": {
                    "rpc": self.client.config.rpc_endpoint,
                    "rest": self.client.config.rest_endpoint,
                    "websocket": self.client.config.websocket_endpoint
                }
            }
        except Exception as e:
            logger.error(f"Failed to get network info summary: {e}")
            return {"error": str(e)}
    
    def estimate_transaction_fee(
        self,
        gas_limit: int,
        gas_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Estimate transaction fee.
        
        Args:
            gas_limit: Gas limit for transaction
            gas_price: Gas price (defaults to network minimum)
            
        Returns:
            Fee estimation
        """
        try:
            if gas_price is None:
                gas_price = self.client.config.fee_minimum_gas_price
            
            fee_amount = int(gas_limit * gas_price)
            
            return {
                "gas_limit": gas_limit,
                "gas_price": gas_price,
                "fee_amount": fee_amount,
                "fee_denom": self.client.config.fee_denom,
                "formatted_fee": self.format_token_amount(fee_amount, self.client.config.fee_denom)
            }
        except Exception as e:
            logger.error(f"Failed to estimate transaction fee: {e}")
            return {"error": str(e)}
    
    # Model and inference utilities (Allora-specific)
    
    def generate_model_id(self, model_name: str, version: str, owner: str) -> str:
        """
        Generate a unique model ID.
        
        Args:
            model_name: Name of the model
            version: Model version
            owner: Model owner address
            
        Returns:
            Unique model ID
        """
        try:
            data = f"{model_name}:{version}:{owner}"
            return self.hash_data(data)[:16]  # Use first 16 characters
        except Exception as e:
            logger.error(f"Failed to generate model ID: {e}")
            raise ValueError("Invalid model parameters")
    
    def validate_model_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate model metadata format.
        
        Args:
            metadata: Model metadata to validate
            
        Returns:
            Validation result with errors if any
        """
        errors = []
        required_fields = ["name", "version", "description", "model_type"]
        
        for field in required_fields:
            if field not in metadata:
                errors.append(f"Missing required field: {field}")
        
        # Validate field types
        if "name" in metadata and not isinstance(metadata["name"], str):
            errors.append("Model name must be a string")
        
        if "version" in metadata and not isinstance(metadata["version"], str):
            errors.append("Model version must be a string")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors
        }
    
    # JSON utilities
    
    def pretty_print_json(self, data: Dict[str, Any], indent: int = 2) -> str:
        """
        Pretty print JSON data.
        
        Args:
            data: Data to format
            indent: Indentation level
            
        Returns:
            Formatted JSON string
        """
        try:
            return json.dumps(data, indent=indent, sort_keys=True, default=str)
        except Exception as e:
            logger.error(f"Failed to format JSON: {e}")
            return str(data)
    
    def safe_json_loads(self, json_str: str) -> Optional[Dict[str, Any]]:
        """
        Safely load JSON string.
        
        Args:
            json_str: JSON string to parse
            
        Returns:
            Parsed JSON or None if invalid
        """
        try:
            return json.loads(json_str)
        except Exception as e:
            logger.debug(f"Failed to parse JSON: {e}")
            return None