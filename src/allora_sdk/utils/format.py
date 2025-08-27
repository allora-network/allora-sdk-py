"""
Formatting utilities for Allora SDK.
"""

from decimal import Decimal


def format_allo_from_uallo(uallo_amount: str | int, decimals: int = 18) -> str:
    """
    Convert uALLO (micro ALLO) to ALLO denomination with proper formatting.
    
    Args:
        uallo_amount: Amount in uALLO as string or int
        decimals: Number of decimal places (default 18 for ALLO)
        
    Returns:
        Formatted string showing ALLO amount
        
    Examples:
        >>> format_allo_from_uallo("1000000000000000000")  # 1e18 uALLO
        "1.000000 ALLO"
        >>> format_allo_from_uallo("500000000000000000")   # 0.5e18 uALLO  
        "0.500000 ALLO"
        >>> format_allo_from_uallo("1234567890123456789")
        "1.234568 ALLO"
    """
    if isinstance(uallo_amount, str):
        uallo_decimal = Decimal(uallo_amount)
    else:
        uallo_decimal = Decimal(str(uallo_amount))
    
    # Convert from micro denomination to base denomination
    divisor = Decimal(10) ** decimals
    allo_amount = uallo_decimal / divisor
    
    # Format with 6 decimal places for readability
    return f"{allo_amount:.6f} ALLO"


def format_allo_from_uallo_short(uallo_amount: str | int, decimals: int = 18) -> str:
    """
    Convert uALLO to ALLO with shorter formatting (fewer decimal places).
    
    Args:
        uallo_amount: Amount in uALLO as string or int
        decimals: Number of decimal places (default 18 for ALLO)
        
    Returns:
        Formatted string showing ALLO amount with 2 decimal places
        
    Examples:
        >>> format_allo_from_uallo_short("1000000000000000000")
        "1.00 ALLO"
        >>> format_allo_from_uallo_short("1234567890123456789") 
        "1.23 ALLO"
    """
    if isinstance(uallo_amount, str):
        uallo_decimal = Decimal(uallo_amount)
    else:
        uallo_decimal = Decimal(str(uallo_amount))
    
    # Convert from micro denomination to base denomination
    divisor = Decimal(10) ** decimals
    allo_amount = uallo_decimal / divisor
    
    # Format with 2 decimal places for compact display
    return f"{allo_amount:.2f} ALLO"