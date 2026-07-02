"""Strongly-typed data models for Tempo transactions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import attrs

from .constants import DEFAULT_CHAIN_ID

if TYPE_CHECKING:
    from .keychain import KeychainSignature
from .types import (
    Address,
    BytesLike,
    as_address,
    as_bytes,
    as_hash32,
    as_optional_address,
)

# Signature validation constants
# ---------------------------------------------------------------------------
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
SECP256K1_HALF_N = SECP256K1_N // 2


# ---------------------------------------------------------------------------
# Call
# ---------------------------------------------------------------------------


@attrs.define(frozen=True)
class Call:
    """Single call in a batch transaction.

    Fields:
        to: Target address (20 bytes). Empty bytes mean contract creation.
        value: Amount to send in wei (must be >= 0).
        data: Call data bytes.
    """

    to: Address = attrs.field(converter=as_address)
    value: int = attrs.field(default=0)
    data: bytes = attrs.field(factory=bytes, converter=as_bytes)

    def __attrs_post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("call.value must be >= 0")

    def as_rlp_list(self) -> list:
        return [bytes(self.to), self.value, self.data]

    @classmethod
    def create(
        cls,
        to: BytesLike,
        value: int = 0,
        data: BytesLike = b"",
    ) -> Call:
        """Create a Call with automatic type coercion."""
        return cls(to=to, value=value, data=data)


# ---------------------------------------------------------------------------
# Access List
# ---------------------------------------------------------------------------


@attrs.define(frozen=True)
class AccessListItem:
    """Single entry in an EIP-2930 access list."""

    address: Address = attrs.field(converter=as_address)
    storage_keys: tuple[bytes, ...] = attrs.field(factory=tuple)

    def __attrs_post_init__(self) -> None:
        if len(bytes(self.address)) != 20:
            raise ValueError("access list address must be 20 bytes")
        self._validate_storage_keys()

    def _validate_storage_keys(self) -> None:
        for k in self.storage_keys:
            if len(k) != 32:
                raise ValueError(f"storage key must be 32 bytes, got {len(k)}")

    def as_rlp_list(self) -> list:
        return [bytes(self.address), [k for k in self.storage_keys]]

    @classmethod
    def create(
        cls,
        address: BytesLike,
        storage_keys: tuple[BytesLike, ...] = (),
    ) -> AccessListItem:
        """Create an AccessListItem with automatic type coercion."""
        keys = tuple(as_hash32(k) for k in storage_keys)
        return cls(address=address, storage_keys=keys)


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def _validate_sig_r(instance: object, attribute: object, value: int) -> None:
    if not (0 < value < SECP256K1_N):
        raise ValueError(f"r must be in (0, secp256k1_n), got {value}")


def _validate_sig_s(instance: object, attribute: object, value: int) -> None:
    if not (0 < value <= SECP256K1_HALF_N):
        raise ValueError(f"s must be in (0, secp256k1_n/2] (low-s), got {value}")


def _validate_sig_v(instance: object, attribute: object, value: int) -> None:
    if value not in (0, 1, 27, 28):
        raise ValueError(f"v must be 0, 1, 27, or 28, got {value}")


@attrs.define(frozen=True)
class Signature:
    """65-byte secp256k1 signature (r || s || v).

    Validates:
    - r in (0, secp256k1_n)
    - s in low-s canonical form: (0, secp256k1_n/2]
    - v is 0, 1, 27, or 28
    """

    r: int = attrs.field(validator=_validate_sig_r)
    s: int = attrs.field(validator=_validate_sig_s)
    v: int = attrs.field(validator=_validate_sig_v)

    @property
    def y_parity(self) -> int:
        """Return normalized recovery id (0 or 1)."""
        return self.v if self.v in (0, 1) else self.v - 27

    def to_bytes(self) -> bytes:
        return self.r.to_bytes(32, "big") + self.s.to_bytes(32, "big") + bytes([self.v])

    def to_canonical_bytes(self) -> bytes:
        """65 bytes with v canonicalized to {27, 28}.

        The node re-encodes embedded secp256k1 signatures with ``v = 27 +
        y_parity`` when recomputing signing hashes and canonical tx bytes, so
        signatures embedded inside payloads must use this form.
        """
        return self.r.to_bytes(32, "big") + self.s.to_bytes(32, "big") + bytes([27 + self.y_parity])

    def to_rlp_list(self) -> list:
        """Return [y_parity, r, s] for RLP encoding (fee_payer_signature)."""
        return [self.y_parity, self.r, self.s]

    @classmethod
    def from_bytes(cls, sig_bytes: bytes) -> Signature:
        """Parse a 65-byte signature and validate r/s/v ranges."""
        if len(sig_bytes) != 65:
            raise ValueError(f"signature must be 65 bytes, got {len(sig_bytes)}")
        r = int.from_bytes(sig_bytes[:32], "big")
        s = int.from_bytes(sig_bytes[32:64], "big")
        v = sig_bytes[64]
        return cls(r=r, s=s, v=v)


# ---------------------------------------------------------------------------
# TempoTransaction
# ---------------------------------------------------------------------------


def _convert_calls(calls: tuple[Call, ...]) -> tuple[Call, ...]:
    return tuple(calls)


def _convert_access_list(
    items: tuple[AccessListItem, ...],
) -> tuple[AccessListItem, ...]:
    return tuple(items)


@attrs.define(frozen=True)
class TempoTransaction:
    """Tempo Transaction (Type 0x76).

    An immutable, strongly-typed representation of a Tempo transaction.

    Features:
    - Four signature types: secp256k1, P256, WebAuthn, Keychain
    - 2D nonce system for parallel transactions
    - Gas sponsorship via fee payer
    - Call batching
    - Optional fee tokens (pay fees in any stablecoin)
    - Transaction validity window (valid_before / valid_after)
    - Access keys with spending limits
    """

    TRANSACTION_TYPE = 0x76
    FEE_PAYER_MAGIC_BYTE = 0x78

    # Core fields
    chain_id: int = DEFAULT_CHAIN_ID
    max_priority_fee_per_gas: int = 0
    max_fee_per_gas: int = 0
    gas_limit: int = 21_000  # renamed from `gas` to align with web3.py conventions

    calls: tuple[Call, ...] = attrs.field(factory=tuple, converter=_convert_calls)
    access_list: tuple[AccessListItem, ...] = attrs.field(factory=tuple, converter=_convert_access_list)

    nonce_key: int = 0
    nonce: int = 0

    valid_before: Optional[int] = None
    valid_after: Optional[int] = None

    fee_token: Optional[Address] = attrs.field(default=None, converter=as_optional_address)

    # Signature fields (a keychain-signed tx carries a KeychainSignature envelope)
    sender_signature: Optional[Signature | KeychainSignature] = None
    fee_payer_signature: Optional[Signature] = None
    awaiting_fee_payer: bool = False

    sender_address: Optional[Address] = attrs.field(default=None, converter=as_optional_address)

    # Authorization list (reserved for EIP-7702)
    tempo_authorization_list: tuple[bytes, ...] = attrs.field(factory=tuple)

    # Key authorization (inline access key provisioning)
    key_authorization: Optional[object] = None  # SignedKeyAuthorization from keychain module

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        chain_id: int = DEFAULT_CHAIN_ID,
        gas_limit: int = 21_000,
        max_fee_per_gas: int = 0,
        max_priority_fee_per_gas: int = 0,
        nonce: int = 0,
        nonce_key: int = 0,
        valid_before: Optional[int] = None,
        valid_after: Optional[int] = None,
        fee_token: Optional[BytesLike] = None,
        awaiting_fee_payer: bool = False,
        calls: tuple[Call, ...] = (),
        access_list: tuple[AccessListItem, ...] = (),
        tempo_authorization_list: tuple[BytesLike, ...] = (),
        key_authorization: Optional[object] = None,
    ) -> TempoTransaction:
        """Create a transaction with automatic type coercion."""
        auth_list = tuple(as_bytes(x) for x in tempo_authorization_list)
        return cls(
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            nonce=nonce,
            nonce_key=nonce_key,
            valid_before=valid_before,
            valid_after=valid_after,
            fee_token=fee_token,
            awaiting_fee_payer=awaiting_fee_payer,
            calls=calls,
            access_list=access_list,
            tempo_authorization_list=auth_list,
            key_authorization=key_authorization,
        )

    @classmethod
    def from_dict(cls, d: dict) -> TempoTransaction:
        """Parse a transaction from a dict with camelCase or snake_case keys.

        Supports both the batched ``calls`` format and the legacy single-call
        ``to``/``value``/``data`` format.
        """

        def _get(*keys: str, default: object = None) -> object:
            for k in keys:
                if k in d:
                    return d[k]
            return default

        chain_id = _get("chainId", "chain_id", default=DEFAULT_CHAIN_ID)
        max_priority_fee = _get("maxPriorityFeePerGas", "max_priority_fee_per_gas", default=0)
        max_fee = _get("maxFeePerGas", "max_fee_per_gas", default=0)
        gas_limit = _get("gas", "gasLimit", "gas_limit", default=21_000)

        # Parse calls
        calls_data = _get("calls", default=[])
        if not calls_data:
            to_addr = _get("to", default="")
            value = _get("value", default=0)
            data = _get("data", default="0x")
            if to_addr or value:
                calls_data = [{"to": to_addr, "value": value, "data": data}]

        calls = tuple(
            Call.create(
                to=call.get("to", ""),
                value=call.get("value", 0),
                data=call.get("data", call.get("input", "0x")),
            )
            for call in list(calls_data)
            if isinstance(calls_data, (list, tuple))
        )

        # Parse access list
        access_list_data = _get("accessList", "access_list", default=[])
        access_list = tuple(
            AccessListItem.create(
                address=item.get("address", ""),
                storage_keys=item.get("storageKeys", item.get("storage_keys", ())),
            )
            for item in list(access_list_data)
            if isinstance(access_list_data, (list, tuple))
        )

        nk = _get("nonceKey", "nonce_key", default=0)
        nonce_key = nk if nk is not None else 0
        nonce = _get("nonce", default=0) or 0

        return cls(
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee,
            max_priority_fee_per_gas=max_priority_fee,
            nonce=nonce,
            nonce_key=nonce_key,
            valid_before=_get("validBefore", "valid_before", default=None),
            valid_after=_get("validAfter", "valid_after", default=None),
            fee_token=_get("feeToken", "fee_token", default=None),
            awaiting_fee_payer=_get("awaitingFeePayer", "awaiting_fee_payer", default=False),
            calls=calls,
            access_list=access_list,
            tempo_authorization_list=_get("tempoAuthorizationList", "tempo_authorization_list", default=()),
            key_authorization=_get("keyAuthorization", "key_authorization", default=None),
        )

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def has_sender_signature(self) -> bool:
        return self.sender_signature is not None

    @property
    def has_fee_payer_signature(self) -> bool:
        return self.fee_payer_signature is not None

    def clone(self) -> TempoTransaction:
        """Create a deep copy of the transaction without signatures."""
        return attrs.evolve(
            self,
            sender_signature=None,
            fee_payer_signature=None,
        )
