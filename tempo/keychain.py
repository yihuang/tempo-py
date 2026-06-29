"""Access key models, key authorization, signing, and signature types.

Provides data models for Tempo's access key system: key restrictions,
spending limits, call scoping, and authorization embedded in transactions.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Optional

import attrs
from eth_utils import keccak, to_bytes

from .models import SECP256K1_HALF_N, SECP256K1_N, Signature, TempoTransaction
from .signer import Signer
from .transaction import get_sign_payload, serialize_for_signing
from .contracts.tip20 import TIP20
from .types import (
    Address,
    BytesLike,
    Selector,
    as_address,
    as_bytes,
    as_selector,
    to_checksum_str,
    validate_nonempty_address,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEYCHAIN_SIGNATURE_TYPE = 0x04
"""Byte identifying a Keychain V2 signature in the transaction envelope."""

KEYCHAIN_SIGNATURE_LENGTH = 86
"""Total byte length of a Keychain V2 signature (inner --- address --- type)."""

INNER_SIGNATURE_LENGTH = 65
"""Byte length of the inner secp256k1 signature (r || s || v)."""


# ---------------------------------------------------------------------------
# SignatureType
# ---------------------------------------------------------------------------


class SignatureType(IntEnum):
    """Supported key types for access keys and signatures.

    Mirrors the Tempo chain's ``SignatureType`` enum.
    """

    SECP256K1 = 0
    """ECDSA over secp256k1 (default)."""
    P256 = 1
    """ECDSA over NIST P-256."""
    WEBAUTHN = 2
    """WebAuthn / FIDO2 signatures."""


# ---------------------------------------------------------------------------
# TokenLimit
# ---------------------------------------------------------------------------


def _validate_token_limit(
    instance: object, attribute: object, value: int
) -> None:
    if value < 0:
        raise ValueError(f"limit/period must be non-negative, got {value}")


@attrs.define(frozen=True)
class TokenLimit:
    """Spending limit for a single token.

    Attributes:
        token: 20-byte token address.
        limit: Maximum amount spendable (0 = no limit).
        period: Time window in seconds for the limit (0 = per-transaction).
    """

    token: Address = attrs.field(converter=as_address, validator=validate_nonempty_address)
    limit: int = attrs.field(validator=_validate_token_limit)
    period: int = attrs.field(default=0, validator=_validate_token_limit)

    @classmethod
    def create(
        cls, token: BytesLike, limit: int, period: int = 0
    ) -> TokenLimit:
        """Construct with type coercion."""
        return cls(token=token, limit=limit, period=period)

    def to_rlp(self) -> list:
        """Encode to an RLP-friendly list ``[token, limit, period]``."""
        return [bytes(self.token), self.limit, self.period]


# ---------------------------------------------------------------------------
# SelectorRule
# ---------------------------------------------------------------------------


@attrs.define(frozen=True)
class SelectorRule:
    """A function-level access rule restricting which recipients are allowed.

    Attributes:
        selector: 4-byte function selector.
        recipients: Tuple of allowed recipient addresses (empty = all).
    """

    selector: bytes = attrs.field(converter=as_bytes)
    recipients: tuple[Address, ...] = attrs.field(
        converter=lambda v: tuple(as_address(a) for a in v)
    )

    @classmethod
    def create(
        cls,
        selector: BytesLike,
        recipients: tuple[BytesLike, ...] = (),
    ) -> SelectorRule:
        """Construct with type coercion."""
        return cls(selector=selector, recipients=recipients)

    def to_rlp(self) -> list:
        """Encode to an RLP-friendly list ``[selector, [recipient, ...]]``."""
        return [bytes(self.selector), [bytes(a) for a in self.recipients]]


# ---------------------------------------------------------------------------
# CallScope
# ---------------------------------------------------------------------------

# TIP-20 function selectors used by CallScope constructors
_ZERO_SELECTOR = bytes(4)


@attrs.define(frozen=True)
class CallScope:
    """Scoped permission for calling a smart contract.

    Attributes:
        target: Contract address the scope applies to.
        selector: 4-byte function selector. ``0x00000000`` = any function.
        selector_rules: Per-selector recipient restrictions.
    """

    target: Address = attrs.field(converter=as_address, validator=validate_nonempty_address)
    selector: bytes = attrs.field(converter=as_bytes)
    selector_rules: tuple[SelectorRule, ...] = attrs.field(
        factory=tuple,
        converter=lambda v: tuple(
            r if isinstance(r, SelectorRule) else SelectorRule.create(**r)  # type: ignore[arg-type]
            for r in v
        ),
    )

    @classmethod
    def transfer(cls, token: Address) -> CallScope:
        """Allow ``transfer(address,uint256)`` on *token*."""
        return cls(
            target=token,
            selector=TIP20.fns.transfer.selector,
            selector_rules=(),
        )

    @classmethod
    def approve(cls, token: Address) -> CallScope:
        """Allow ``approve(address,uint256)`` on *token*."""
        return cls(
            target=token,
            selector=TIP20.fns.approve.selector,
            selector_rules=(),
        )

    @classmethod
    def unrestricted(cls, target: BytesLike) -> CallScope:
        """Allow any function call on *target*."""
        return cls(
            target=target,
            selector=_ZERO_SELECTOR,
            selector_rules=(),
        )

    @classmethod
    def with_selector(
        cls,
        target: BytesLike,
        selector: BytesLike,
        selector_rules: tuple[SelectorRule, ...] = (),
    ) -> CallScope:
        """Allow a specific function selector on *target*, optionally restricted."""
        return cls(
            target=target,
            selector=selector,
            selector_rules=selector_rules,
        )


# ---------------------------------------------------------------------------
# KeyRestrictions
# ---------------------------------------------------------------------------


@attrs.define(frozen=True)
class KeyRestrictions:
    """Spending and call restrictions for an access key.

    Attributes:
        expiry: Unix timestamp when the key expires (0 = never).
        enforce_limits: Whether token spending limits are enforced.
        limits: Per-token spending limits.
        allow_any_calls: If True, any contract call is permitted.
        allowed_calls: Explicit list of allowed call scopes.
    """

    expiry: int = 0
    enforce_limits: bool = False
    limits: tuple[TokenLimit, ...] = attrs.field(
        factory=tuple,
        converter=lambda v: tuple(
            t if isinstance(t, TokenLimit) else TokenLimit.create(**t)  # type: ignore[arg-type]
            for t in v
        ),
    )
    allow_any_calls: bool = False
    allowed_calls: tuple[CallScope, ...] = attrs.field(
        factory=tuple,
        converter=lambda v: tuple(
            c if isinstance(c, CallScope) else CallScope(**c)  # type: ignore[arg-type]
            for c in v
        ),
    )

    @classmethod
    def create(
        cls,
        *,
        expiry: int = 0,
        enforce_limits: bool = False,
        limits: tuple[TokenLimit, ...] = (),
        allow_any_calls: bool = False,
        allowed_calls: tuple[CallScope, ...] = (),
    ) -> KeyRestrictions:
        """Construct with convertible arguments."""
        return cls(
            expiry=expiry,
            enforce_limits=enforce_limits,
            limits=limits,
            allow_any_calls=allow_any_calls,
            allowed_calls=allowed_calls,
        )

    def with_limits(self, *limits: TokenLimit) -> KeyRestrictions:
        """Return a copy with the given token limits."""
        return attrs.evolve(
            self,
            enforce_limits=True,
            limits=limits,
        )

    def add_call(self, call_scope: CallScope) -> KeyRestrictions:
        """Return a copy with an additional allowed call scope."""
        return attrs.evolve(
            self,
            allow_any_calls=False,
            allowed_calls=self.allowed_calls + (call_scope,),
        )

    def as_admin(self) -> KeyRestrictions:
        """Return a copy with admin privileges (no restrictions)."""
        return attrs.evolve(
            self,
            expiry=0,
            enforce_limits=False,
            limits=(),
            allow_any_calls=True,
            allowed_calls=(),
        )

    def to_rlp(self) -> list:
        """Encode to an RLP-friendly list."""
        return [
            self.expiry,
            1 if self.enforce_limits else 0,
            [t.to_rlp() for t in self.limits],
            1 if self.allow_any_calls else 0,
            [c.to_rlp() for c in self.allowed_calls],
        ]


# ---------------------------------------------------------------------------
# KeyAuthorization
# ---------------------------------------------------------------------------


@attrs.define(frozen=True)
class KeyAuthorization:
    """Transaction-embedded key authorization provisioned by a root account.

    Attributes:
        chain_id: Chain where this authorization is valid.
        key_type: Type of the access key (``SignatureType``).
        key_id: The access key's address (20 bytes) or public key hash.
        expiry: Unix timestamp when this authorization expires.
        limits: Per-token spending limits (``TokenLimit`` list).
        allowed_calls: List of allowed call scopes.
        witness: Root-account signature over the authorization payload.
        is_admin: Whether this key has unrestricted admin access.
        account: The root account that granted this authorization.
    """

    # Mandatory fields first (no defaults)
    key_id: Address = attrs.field(converter=as_address, validator=validate_nonempty_address)
    account: Address = attrs.field(
        converter=as_address, validator=validate_nonempty_address
    )

    # Fields with defaults
    chain_id: int = 0
    key_type: int = SignatureType.SECP256K1
    expiry: int = 0
    limits: tuple[TokenLimit, ...] = attrs.field(
        factory=tuple,
        converter=lambda v: tuple(
            t if isinstance(t, TokenLimit) else TokenLimit.create(**t)  # type: ignore[arg-type]
            for t in v
        ),
    )
    allowed_calls: tuple[CallScope, ...] = attrs.field(
        factory=tuple,
        converter=lambda v: tuple(
            c if isinstance(c, CallScope) else CallScope(**c)  # type: ignore[arg-type]
            for c in v
        ),
    )
    witness: bytes = attrs.field(factory=bytes, converter=as_bytes)
    is_admin: bool = False

    def to_rlp(self) -> list:
        """Encode to an RLP-friendly list for embedding in a transaction.

        The list omits ``witness`` for signing, but includes it for the final
        payload.  Callers should pass the result of ``to_rlp_signing()`` when
        computing the witness hash and ``to_rlp()`` for the final transaction.
        """
        return [
            self.chain_id,
            self.key_type,
            bytes(self.key_id),
            self.expiry,
            [t.to_rlp() for t in self.limits],
            [c.to_rlp() for c in self.allowed_calls],
            self.witness,
            1 if self.is_admin else 0,
            bytes(self.account),
        ]

    def to_rlp_signing(self) -> list:
        """Encode without the ``witness`` field for signing."""
        return [
            self.chain_id,
            self.key_type,
            bytes(self.key_id),
            self.expiry,
            [t.to_rlp() for t in self.limits],
            [c.to_rlp() for c in self.allowed_calls],
            1 if self.is_admin else 0,
            bytes(self.account),
        ]

    def authorization_hash(self) -> bytes:
        """Compute keccak256 hash of the ABI-encoded authorization fields.

        This is the payload the root account signs to produce the *witness*.
        """
        raw = _abi_encode_authorization(
            chain_id=self.chain_id,
            key_type=self.key_type,
            key_id=bytes(self.key_id),
            expiry=self.expiry,
            limits=self.limits,
            allowed_calls=self.allowed_calls,
            is_admin=self.is_admin,
            account=bytes(self.account),
        )
        return keccak(raw)


# ---------------------------------------------------------------------------
# KeychainSignature
# ---------------------------------------------------------------------------


@attrs.define(frozen=True)
class KeychainSignature:
    """86-byte Keychain V2 signature.

    Layout (86 bytes)::

        [0:65]   inner secp256k1 signature (r || s || v)
        [65:85]  user address (20 bytes)
        [85:86]  key type byte (0 = SECP256K1, 1 = P256, 2 = WEBAUTHN)

    Attributes:
        raw: Full 86-byte keychain signature blob.
    """

    raw: bytes = attrs.field(converter=as_bytes)

    def __attrs_post_init__(self) -> None:
        if len(self.raw) != KEYCHAIN_SIGNATURE_LENGTH:
            raise ValueError(
                f"KeychainSignature must be {KEYCHAIN_SIGNATURE_LENGTH} bytes, "
                f"got {len(self.raw)}"
            )

    @classmethod
    def from_inner(
        cls,
        inner_sig: Signature,
        user_address: BytesLike,
        key_type: SignatureType = SignatureType.SECP256K1,
    ) -> KeychainSignature:
        """Build from an inner secp256k1 signature and user address."""
        raw = inner_sig.to_bytes()  # 65 bytes
        raw += bytes(as_address(user_address))  # 20 bytes
        raw += bytes([int(key_type)])  # 1 byte
        return cls(raw=raw)

    @property
    def inner_signature(self) -> Signature:
        """Extract the 65-byte inner secp256k1 signature."""
        raw = self.raw[:INNER_SIGNATURE_LENGTH]
        r = int.from_bytes(raw[0:32], "big")
        s = int.from_bytes(raw[32:64], "big")
        v = raw[64]
        return Signature(r=r, s=s, v=v)

    @property
    def user_address(self) -> Address:
        """Extract the 20-byte user address."""
        return Address(self.raw[INNER_SIGNATURE_LENGTH : INNER_SIGNATURE_LENGTH + 20])

    @property
    def key_type(self) -> SignatureType:
        """Extract the key type byte."""
        return SignatureType(self.raw[KEYCHAIN_SIGNATURE_LENGTH - 1])

    def to_rlp(self) -> bytes:
        """Return raw bytes for RLP encoding."""
        return self.raw


# ---------------------------------------------------------------------------
# ABI encoding helpers
# ---------------------------------------------------------------------------


def _pad32(data: bytes) -> bytes:
    """Left-pad *data* to 32 bytes (zero padding for uint/int/address)."""
    if len(data) > 32:
        raise ValueError(f"data too long for _pad32: {len(data)} bytes")
    return b"\x00" * (32 - len(data)) + data


def _abi_encode_authorization(
    *,
    chain_id: int,
    key_type: int,
    key_id: bytes,
    expiry: int,
    limits: tuple[TokenLimit, ...],
    allowed_calls: tuple[CallScope, ...],
    is_admin: bool,
    account: bytes,
) -> bytes:
    """Encode a key authorization for hashing using Solidity-style encoding.

    Uses a simple concatenation + keccak scheme::

        keccak(
            abi.encode(chain_id, key_type, key_id, expiry, limits, allowed_calls, is_admin, account)
        )

    Since we avoid an ABI encoder dependency, we flatten the structure
    following ABI encoding rules manually.
    """
    parts: list[bytes] = []

    # Static head: chain_id, key_type, key_id, expiry
    parts.append(_pad32(chain_id.to_bytes(32, "big")))
    parts.append(_pad32(key_type.to_bytes(32, "big")))
    parts.append(_pad32(key_id))  # address or hash → left-padded to 32
    parts.append(_pad32(expiry.to_bytes(32, "big")))

    # Dynamic: limits[] — at known offset (head is 8×32 = 256 bytes + 2 offsets)
    # We skip complex ABI offset encoding and instead hash each array element
    # separately, then hash the concatenation of hashes.
    limit_hashes = b"".join(
        keccak(
            _pad32(t.token)
            + _pad32(t.limit.to_bytes(32, "big"))
            + _pad32(t.period.to_bytes(32, "big"))
        )
        for t in limits
    )
    parts.append(_pad32(keccak(limit_hashes) if limit_hashes else bytes(32)))

    # Dynamic: allowed_calls[]
    call_hashes = b"".join(
        keccak(
            _pad32(cs.target)
            + _pad32(cs.selector)
            + _encode_selector_rules(cs.selector_rules)
        )
        for cs in allowed_calls
    )
    parts.append(_pad32(keccak(call_hashes) if call_hashes else bytes(32)))

    # Bool + address
    parts.append(_pad32(bytes([1 if is_admin else 0])))
    parts.append(_pad32(account))

    return b"".join(parts)


def _encode_selector_rules(rules: tuple[SelectorRule, ...]) -> bytes:
    """Encode a list of SelectorRule for hashing."""
    rule_hashes = b"".join(
        keccak(
            _pad32(r.selector)
            + _encode_address_array(r.recipients)
        )
        for r in rules
    )
    return keccak(rule_hashes) if rule_hashes else bytes(32)


def _encode_address_array(addrs: tuple[Address, ...]) -> bytes:
    """Encode an address array for hashing."""
    if not addrs:
        return bytes(32)
    packed = b"".join(_pad32(bytes(a)) for a in addrs)
    return keccak(packed)


# ---------------------------------------------------------------------------
# Keychain signature construction
# ---------------------------------------------------------------------------


def build_keychain_signature(
    inner_sig: Signature,
    user_address: BytesLike,
) -> bytes:
    """Build a 86-byte Keychain V2 signature blob.

    The returned byte string is ``r(32) || s(32) || v(1) || address(20) ||
    key_type(1)`` suitable for embedding as a raw signature or RLP field.

    Args:
        inner_sig: The 65-byte secp256k1 signature (``Signature``).
        user_address: The 20-byte address of the signer.

    Returns:
        86 bytes conforming to the Keychain V2 signature format.
    """
    sig_raw = inner_sig.to_bytes()  # 65 bytes
    addr_raw = bytes(as_address(user_address))  # 20 bytes
    return sig_raw + addr_raw + bytes([SignatureType.SECP256K1])


# ---------------------------------------------------------------------------
# Key authorization creation
# ---------------------------------------------------------------------------


def create_key_authorization(
    *,
    chain_id: int,
    key_type: SignatureType = SignatureType.SECP256K1,
    key_id: BytesLike,
    expiry: int = 0,
    limits: tuple[TokenLimit, ...] = (),
    allowed_calls: tuple[CallScope, ...] = (),
    witness: BytesLike = b"",
    is_admin: bool = False,
    account: BytesLike,
) -> list:
    """Create an RLP-encodable key authorization list for a transaction.

    The returned list is suitable for passing as ``key_authorization`` to
    :meth:`TempoTransaction.create` or the :class:`Builder`.

    Example::

        auth = create_key_authorization(
            chain_id=CHAIN_ID_MODERATO,
            key_id=access_key.address,
            is_admin=True,
            account=root_signer.address,
        )
        tx = TempoTransaction.create(key_authorization=auth, ...)

    Args:
        chain_id: Target chain ID.
        key_type: Signature/access key type.
        key_id: Access key address (20 bytes) or public key hash.
        expiry: Unix timestamp when this authorization expires (0 = never).
        limits: Per-token spending limits.
        allowed_calls: Allowed call scopes.
        witness: Root-account signature over the authorization (empty for
            pre-signing construction; the caller should compute and set
            this once the authorization is signed).
        is_admin: Whether this key has unrestricted admin access.
        account: The root account granting the authorization.

    Returns:
        An RLP-encodable list of the form
        ``[chain_id, key_type, key_id, expiry, limits[], allowed_calls[],
        witness, is_admin, account]``.
    """
    kw = KeyAuthorization(
        chain_id=chain_id,
        key_type=int(key_type),
        key_id=key_id,
        expiry=expiry,
        limits=limits,
        allowed_calls=allowed_calls,
        witness=witness,
        is_admin=is_admin,
        account=account,
    )
    return kw.to_rlp()


# ---------------------------------------------------------------------------
# Sign transaction with access key
# ---------------------------------------------------------------------------


def sign_tx_access_key(
    tx: TempoTransaction,
    access_key_sk: str,
    root_account: Signer,
    *,
    chain_id: Optional[int] = None,
    key_type: SignatureType = SignatureType.SECP256K1,
    key_id: Optional[BytesLike] = None,
    expiry: int = 0,
    limits: tuple[TokenLimit, ...] = (),
    allowed_calls: tuple[CallScope, ...] = (),
    is_admin: bool = True,
) -> TempoTransaction:
    """Sign a transaction with an access key, provisioning it inline.

    This is a convenience that:

    1. Creates a :class:`KeyAuthorization` describing what the access key
       is allowed to do.
    2. Signs the authorization with the *root_account* to produce a witness.
    3. Signs the transaction payload with the access key.
    4. Returns a fully-signed :class:`TempoTransaction` with both the
       key authorization and the sender signature attached.

    Args:
        tx: Unsigned or partially-built transaction. Its ``key_authorization``
            and ``sender_signature`` will be replaced.
        access_key_sk: Hex-encoded private key of the access key (secp256k1).
        root_account: The root ``Signer`` that grants the authorization.
        chain_id: Chain ID (defaults to ``tx.chain_id``).
        key_type: Type of the access key.
        key_id: Access key address (defaults to signing key's address).
        expiry: Unix timestamp for expiry (0 = never).
        limits: Per-token spending limits.
        allowed_calls: Allowed call scopes.
        is_admin: Whether the key has unrestricted admin access.

    Returns:
        A new ``TempoTransaction`` with ``key_authorization`` and
        ``sender_signature`` set.
    """
    access_key = Signer(access_key_sk)
    actual_chain_id = chain_id if chain_id is not None else tx.chain_id
    actual_key_id = as_address(key_id) if key_id is not None else access_key.address

    # Build the unsigned authorization
    auth = KeyAuthorization(
        chain_id=actual_chain_id,
        key_type=int(key_type),
        key_id=actual_key_id,
        expiry=expiry,
        limits=limits,
        allowed_calls=allowed_calls,
        witness=b"",
        is_admin=is_admin,
        account=root_account.address,
    )

    # Root account signs the authorization hash → witness
    auth_hash = auth.authorization_hash()
    witness_sig = root_account.sign(auth_hash)
    witness_bytes = witness_sig.to_bytes()

    # Create the final authorization with witness attached
    signed_auth = attrs.evolve(auth, witness=witness_bytes)

    # Attach authorization to the transaction, sign the tx payload
    tx_with_auth = tx._replace_fields(
        key_authorization=signed_auth.to_rlp(),
    )

    # Sign the transaction payload with the access key
    payload_hash = get_sign_payload(tx_with_auth)
    access_sig = access_key.sign(payload_hash)

    return tx_with_auth._replace_fields(
        sender_signature=access_sig,
        sender_address=access_key.address,
    )
