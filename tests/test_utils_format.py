"""
Tests for formatting utilities (allo <-> uallo conversions).
"""
import pytest
from decimal import Decimal

from allora_sdk.utils.format import (
    allo_to_uallo,
    uallo_to_allo,
    format_allo_from_uallo,
    format_allo_from_uallo_short,
)

ONE_ALLO_IN_UALLO = 10**18


class TestAlloToUallo:
    def test_string_input(self):
        assert allo_to_uallo("1.0") == ONE_ALLO_IN_UALLO
        assert allo_to_uallo("0.5") == 500000000000000000

    def test_float_input(self):
        assert allo_to_uallo(1.0) == ONE_ALLO_IN_UALLO
        assert allo_to_uallo(0.5) == 500000000000000000

    def test_int_input(self):
        assert allo_to_uallo(1) == ONE_ALLO_IN_UALLO
        assert allo_to_uallo(100) == 100 * ONE_ALLO_IN_UALLO

    def test_decimal_input(self):
        assert allo_to_uallo(Decimal("2.345678901234567890")) == 2345678901234567890

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError):
            allo_to_uallo([1])  # type: ignore
        with pytest.raises(TypeError):
            allo_to_uallo(None)  # type: ignore


class TestUalloToAllo:
    def test_string_input(self):
        assert uallo_to_allo("1000000000000000000") == Decimal("1")
        assert uallo_to_allo("500000000000000000") == Decimal("0.5")

    def test_int_input(self):
        assert uallo_to_allo(ONE_ALLO_IN_UALLO) == Decimal("1")

    def test_fractional_allo(self):
        result = uallo_to_allo("1234567890123456789")
        assert result == Decimal("1234567890123456789") / Decimal(10**18)

    def test_custom_decimals(self):
        assert uallo_to_allo("1000000", decimals=6) == Decimal("1")


class TestFormatAlloFromUallo:
    def test_one_allo(self):
        result = format_allo_from_uallo("1000000000000000000")
        assert result.endswith(" ALLO")
        allo_value = Decimal(result.split()[0])
        assert allo_value == Decimal("1")

    def test_fractional(self):
        result = format_allo_from_uallo("500000000000000000")
        assert result.endswith(" ALLO")
        allo_value = Decimal(result.split()[0])
        assert allo_value == Decimal("0.5")

    def test_int_input(self):
        result = format_allo_from_uallo(ONE_ALLO_IN_UALLO)
        assert "ALLO" in result


class TestFormatAlloFromUalloShort:
    def test_one_allo(self):
        assert format_allo_from_uallo_short("1000000000000000000") == "1.00 ALLO"

    def test_truncates_to_two_decimals(self):
        assert format_allo_from_uallo_short("1234567890123456789") == "1.23 ALLO"

    def test_zero(self):
        assert format_allo_from_uallo_short("0") == "0.00 ALLO"


class TestRoundTrip:
    def test_round_trip_preserves_value(self):
        """allo -> uallo -> allo should preserve value for clean decimals."""
        for original in ["1.0", "0.5", "100", "0.000000000000000001"]:
            uallo = allo_to_uallo(original)
            recovered = uallo_to_allo(uallo)
            assert recovered == Decimal(original), f"Round-trip failed for {original}"


class TestEdgeCases:
    def test_zero(self):
        assert allo_to_uallo(0) == 0
        assert uallo_to_allo(0) == Decimal("0")

    def test_very_small_amount(self):
        assert allo_to_uallo("0.000000000000000001") == 1

    def test_very_large_amount(self):
        large = "999999999999"
        uallo = allo_to_uallo(large)
        assert uallo == int(Decimal(large) * Decimal(10**18))
        recovered = uallo_to_allo(uallo)
        assert recovered == Decimal(large)
