"""
Formatting utilities for Allora SDK.
"""

from decimal import Decimal

def allo_to_uallo(allo: str | Decimal | float | int):
    """
    Convert ALLO amount to uALLO (micro ALLO) integer representation.

    Args:
        allo: Amount in ALLO as string, Decimal, float, or int

    Returns:
        Amount in uALLO as an integer

    Examples:
        >>> allo_to_uallo(1.0)
        1000000000000000000
        >>> allo_to_uallo("0.5")
        500000000000000000
        >>> allo_to_uallo(Decimal("2.345678901234567890"))
        2345678901234567890
    """
    if isinstance(allo, str):
        allo_decimal = Decimal(allo)
    elif isinstance(allo, (float, int)):
        allo_decimal = Decimal(str(allo))
    elif isinstance(allo, Decimal):
        allo_decimal = allo
    else:
        raise TypeError("allo must be of type str, Decimal, float, or int")

    # Convert from ALLO to uALLO by multiplying by 10^18
    uallo_decimal = allo_decimal * (Decimal(10) ** 18)

    # Return as big int
    return int(uallo_decimal)

def uallo_to_allo(uallo: str | int, decimals: int = 18) -> Decimal:
    """
    Convert uALLO (micro ALLO) to ALLO denomination.

    Args:
        uallo: Amount in uALLO as string or int
        decimals: Number of decimal places (default 18 for ALLO)

    Returns:
        Amount in ALLO as Decimal

    Examples:
        >>> uallo_to_allo("1000000000000000000")  # 1e18 uALLO
        Decimal('1')
        >>> uallo_to_allo("500000000000000000")   # 0.5e18 uALLO
        Decimal('0.5')
        >>> uallo_to_allo("1234567890123456789")
        Decimal('1.234567890123456789')
    """
    if isinstance(uallo, str):
        uallo_decimal = Decimal(uallo)
    else:
        uallo_decimal = Decimal(str(uallo))

    # Convert from micro denomination to base denomination
    divisor = Decimal(10) ** decimals
    allo_decimal = uallo_decimal / divisor

    return allo_decimal

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
    return f"{allo_amount:.18f} ALLO"


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