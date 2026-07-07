"""Type definitions and coercion helpers for Tempo transactions."""

from typing import NewType

from eth_utils import to_bytes, to_checksum_address

Address = NewType("Address", bytes)
Hash32 = NewType("Hash32", bytes)
Selector = NewType("Selector", bytes)

BytesLike = bytes | str | bytearray | memoryview


def as_bytes(value: BytesLike) -> bytes:
    """Convert hex string, bytes, bytearray, or memoryview to bytes.

    Raises:
        TypeError: If value is not a string or bytes-like object.
    """
    if isinstance(value, str):
        if value in ("", "0x"):
            return b""
        return to_bytes(hexstr=value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    raise TypeError(f"expected str, bytes, bytearray, or memoryview, got {type(value).__name__}")


def as_address(value: BytesLike) -> Address:
    """Convert hex string or bytes to a validated 20-byte address.

    Raises:
        TypeError: If value is not a string or bytes-like object.
        ValueError: If address is not 0 or 20 bytes.
    """
    if isinstance(value, str):
        if value in ("", "0x"):
            return Address(b"")
        b = to_bytes(hexstr=value)
    elif isinstance(value, (bytes, bytearray, memoryview)):
        b = bytes(value)
    else:
        raise TypeError(f"expected str, bytes, bytearray, or memoryview, got {type(value).__name__}")
    if len(b) not in (0, 20):
        raise ValueError(f"address must be 20 bytes (or empty), got {len(b)}")
    return Address(b)


def as_optional_address(value: BytesLike | None) -> Address | None:
    """Convert to Address, treating empty/None as None."""
    if value is None:
        return None
    b = as_bytes(value)
    if b == b"":
        return None
    return as_address(b)


def as_hash32(value: BytesLike) -> Hash32:
    """Convert hex string or bytes to a validated 32-byte hash.

    Raises:
        TypeError: If value is not a string or bytes-like object.
        ValueError: If hash is not exactly 32 bytes.
    """
    if isinstance(value, str):
        b = to_bytes(hexstr=value)
    elif isinstance(value, (bytes, bytearray, memoryview)):
        b = bytes(value)
    else:
        raise TypeError(f"expected str, bytes, bytearray, or memoryview, got {type(value).__name__}")
    if len(b) != 32:
        raise ValueError(f"hash32 must be 32 bytes, got {len(b)}")
    return Hash32(b)


def as_selector(value: BytesLike) -> Selector:
    """Convert hex string or bytes to a validated 4-byte function selector."""
    b = as_bytes(value)
    if len(b) != 4:
        raise ValueError(f"selector must be exactly 4 bytes, got {len(b)}")
    return Selector(b)


def validate_nonempty_address(instance: object, attribute: object, value: Address) -> None:
    """Attrs validator: address must be exactly 20 bytes (not empty)."""
    if len(bytes(value)) != 20:
        raise ValueError("address must be exactly 20 bytes")


def to_checksum_str(addr: BytesLike) -> str:
    """Convert address bytes or hex to checksummed hex string."""
    raw = as_bytes(addr)
    if not raw:
        return "0x" + "00" * 20
    return to_checksum_address(raw)
