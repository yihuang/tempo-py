"""Tests for tempo.types."""

import pytest
from tempo.types import as_address, as_bytes, as_hash32, as_selector, as_optional_address


class TestAsBytes:
    def test_hex_string(self):
        assert as_bytes("0xabcdef") == b"\xab\xcd\xef"

    def test_empty_hex_string(self):
        assert as_bytes("") == b""
        assert as_bytes("0x") == b""

    def test_raw_bytes(self):
        assert as_bytes(b"\x01\x02") == b"\x01\x02"

    def test_rejects_int(self):
        with pytest.raises(TypeError):
            as_bytes(123)  # type: ignore[arg-type]


class TestAsAddress:
    def test_hex_string(self):
        addr = "0x" + "ab" * 20
        result = as_address(addr)
        assert len(result) == 20
        assert result == b"\xab" * 20

    def test_empty_returns_empty(self):
        assert as_address("") == b""
        assert as_address("0x") == b""

    def test_rejects_wrong_length(self):
        with pytest.raises(ValueError, match="must be 20 bytes"):
            as_address("0x1234")


class TestAsHash32:
    def test_valid(self):
        h = as_hash32("0x" + "01" * 32)
        assert len(h) == 32

    def test_rejects_wrong_length(self):
        with pytest.raises(ValueError, match="must be 32 bytes"):
            as_hash32("0x1234")


class TestAsSelector:
    def test_valid(self):
        s = as_selector("0xa9059cbb")
        assert len(s) == 4

    def test_rejects_wrong_length(self):
        with pytest.raises(ValueError, match="must be exactly 4 bytes"):
            as_selector("0x1234")


class TestAsOptionalAddress:
    def test_none(self):
        assert as_optional_address(None) is None

    def test_empty(self):
        assert as_optional_address(b"") is None

    def test_valid(self):
        addr = as_optional_address("0x" + "ab" * 20)
        assert len(addr) == 20  # type: ignore[arg-type]
