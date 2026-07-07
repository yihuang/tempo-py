"""Access key models, key authorization, signing, and signature types.

Provides data models for Tempo's access key system: key restrictions,
spending limits, call scoping, and authorization embedded in transactions.
"""

from __future__ import annotations

from enum import IntEnum

import attrs
import rlp
from eth_utils import keccak

from .contracts.tip20 import TIP20
from .models import Signature, TempoTransaction
from .signer import Signer
from .transaction import get_sign_payload
from .types import (
    Address,
    BytesLike,
    as_address,
    as_bytes,
    as_optional_address,
    validate_nonempty_address,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEYCHAIN_SIGNATURE_TYPE = 0x04
"""Byte identifying a Keychain V2 signature in the transaction envelope."""

INNER_SIGNATURE_LENGTH = 65
"""Byte length of the inner secp256k1 signature (r || s || v)."""

KEYCHAIN_SIGNATURE_LENGTH = 1 + 20 + INNER_SIGNATURE_LENGTH
"""Total byte length of a Keychain V2 signature: type(1) || address(20) || inner(65)."""

_INNER_OFFSET = KEYCHAIN_SIGNATURE_LENGTH - INNER_SIGNATURE_LENGTH  # 21


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


def _validate_token_limit(instance: object, attribute: object, value: int) -> None:
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
    def create(cls, token: BytesLike, limit: int, period: int = 0) -> TokenLimit:
        """Construct with type coercion."""
        return cls(token=token, limit=limit, period=period)

    def to_rlp(self) -> list:
        """Encode to ``[token, limit, period?]``.

        The node wire form is trailing-canonical with ``period:
        Option<NonZeroU64>``: period 0 must be omitted (a trailing ``0x80``
        placeholder is rejected as a zero ``NonZeroU64``).
        """
        if self.period == 0:
            return [bytes(self.token), self.limit]
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
    recipients: tuple[Address, ...] = attrs.field(converter=lambda v: tuple(as_address(a) for a in v))

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

    def to_rlp(self) -> list:
        """Encode to the node wire form ``[target, [selector_rule, ...]]``.

        The wire has no top-level selector: empty ``selector_rules`` means the
        key may call ANY function on the target, so a plain ``selector`` (from
        ``transfer()``/``approve()``/``with_selector()``) is folded into a
        single rule; only the zero selector (``unrestricted()``) encodes no
        rules.
        """
        rules = self.selector_rules
        if not rules and self.selector != _ZERO_SELECTOR:
            rules = (SelectorRule(selector=self.selector, recipients=()),)
        return [bytes(self.target), [r.to_rlp() for r in rules]]

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
        chain_id: Chain where this authorization is valid (must match the
            target chain; the node rejects mismatches).
        key_type: Type of the access key (``SignatureType``).
        key_id: The access key's address (20 bytes) or public key hash.
        expiry: Unix timestamp when this authorization expires (0 = never).
        limits: Per-token spending limits. ``None`` = unlimited spending,
            ``()`` = deny all spending.
        allowed_calls: Allowed call scopes. ``None`` = unrestricted calls,
            ``()`` = deny all calls.
        witness: Optional 32-byte TIP-1053 witness (empty = none). The root
            grant signature does NOT go here; it rides separately in the
            ``SignedKeyAuthorization`` wrapper.
        is_admin: Whether this key has unrestricted admin access. Admin keys
            must not carry expiry, limits, or call scopes.
        account: The root account that granted this authorization
            (``None`` = omitted; required for admin-signed grants).

    For more info (KeyAuthorization signature_hash = keccak256(rlp), and
    KeyAuthorizationWire trailing-canonical layout):
    https://github.com/tempoxyz/tempo/blob/d0b4ca4/crates/primitives/src/transaction/key_authorization.rs#L316
    """

    # Mandatory fields first (no defaults)
    key_id: Address = attrs.field(converter=as_address, validator=validate_nonempty_address)
    chain_id: int = attrs.field(validator=attrs.validators.instance_of(int))

    # Fields with defaults
    account: Address | None = attrs.field(default=None, converter=as_optional_address)
    key_type: int = SignatureType.SECP256K1
    expiry: int = 0
    limits: tuple[TokenLimit, ...] | None = attrs.field(
        default=None,
        converter=lambda v: (
            v
            if v is None
            else tuple(
                t if isinstance(t, TokenLimit) else TokenLimit.create(**t)  # type: ignore[arg-type]
                for t in v
            )
        ),
    )
    allowed_calls: tuple[CallScope, ...] | None = attrs.field(
        default=None,
        converter=lambda v: (
            v
            if v is None
            else tuple(
                c if isinstance(c, CallScope) else CallScope(**c)  # type: ignore[arg-type]
                for c in v
            )
        ),
    )
    witness: bytes = attrs.field(factory=bytes, converter=as_bytes)
    is_admin: bool = False

    def __attrs_post_init__(self) -> None:
        if self.witness and len(self.witness) != 32:
            raise ValueError(f"witness must be 32 bytes when set, got {len(self.witness)}")
        if self.is_admin and (self.expiry or self.limits is not None or self.allowed_calls is not None):
            # Mirrors the node: "admin key authorizations cannot carry expiry,
            # limits, or call scopes".
            raise ValueError("admin KeyAuthorization must not set expiry, limits, or allowed_calls")

    def to_rlp(self) -> list:
        """Encode to the node's canonical trailing-form RLP list.

        Wire order (the node uses ``#[rlp(trailing(canonical))]``)::

            [chain_id, key_type, key_id, expiry?, limits?, allowed_calls?,
             witness?, is_admin?, account?]

        Absent trailing optionals are omitted; an absent optional that precedes
        a present later field is emitted as an empty string (``0x80``).
        """
        head: list = [self.chain_id, int(self.key_type), bytes(self.key_id)]
        optionals: list = [
            self.expiry or None,
            [t.to_rlp() for t in self.limits] if self.limits is not None else None,
            [c.to_rlp() for c in self.allowed_calls] if self.allowed_calls is not None else None,
            bytes(self.witness) if self.witness else None,
            1 if self.is_admin else None,
            bytes(self.account) if self.account is not None else None,
        ]
        last = max((i for i, v in enumerate(optionals) if v is not None), default=-1)
        head.extend(v if v is not None else b"" for v in optionals[: last + 1])
        return head

    def signature_hash(self) -> bytes:
        """keccak256 of the RLP-encoded authorization -- the payload the root signs."""
        return keccak(rlp.encode(self.to_rlp()))


# ---------------------------------------------------------------------------
# KeychainSignature
# ---------------------------------------------------------------------------


@attrs.define(frozen=True)
class KeychainSignature:
    """86-byte Keychain V2 signature.

    Layout (86 bytes)::

        [0:1]    type byte (0x04 = Keychain V2)
        [1:21]   root account address (20 bytes)
        [21:86]  inner secp256k1 signature (r || s || v)

    The access key signs ``keccak256(0x04 || sig_hash || root_account)``; the
    ``0x04`` domain separator prevents cross-scheme signature confusion.

    Attributes:
        raw: Full 86-byte keychain signature blob.

    For more info (Keychain V2 signature):
    https://github.com/tempoxyz/tempo/blob/d0b4ca4/crates/primitives/src/transaction/tt_signature.rs#L514
    """

    raw: bytes = attrs.field(converter=as_bytes)

    def __attrs_post_init__(self) -> None:
        if len(self.raw) != KEYCHAIN_SIGNATURE_LENGTH:
            raise ValueError(f"KeychainSignature must be {KEYCHAIN_SIGNATURE_LENGTH} bytes, got {len(self.raw)}")
        if self.raw[0] != KEYCHAIN_SIGNATURE_TYPE:
            raise ValueError(f"expected Keychain V2 type byte {KEYCHAIN_SIGNATURE_TYPE:#04x}, got {self.raw[0]:#04x}")

    @classmethod
    def from_inner(
        cls,
        inner_sig: Signature,
        user_address: BytesLike,
    ) -> KeychainSignature:
        """Build from the access key's inner signature and the root account address.

        The inner signature is embedded with canonical v in {27, 28} so the
        serialized transaction matches the node's re-encoded canonical bytes
        (and therefore its tx hash).
        """
        raw = bytes([KEYCHAIN_SIGNATURE_TYPE]) + bytes(as_address(user_address)) + inner_sig.to_canonical_bytes()
        return cls(raw=raw)

    @classmethod
    def signing_hash(cls, sig_hash: bytes, user_address: BytesLike) -> bytes:
        """The hash the access key signs: ``keccak256(0x04 || sig_hash || root)``."""
        if len(sig_hash) != 32:
            raise ValueError(f"sig_hash must be 32 bytes, got {len(sig_hash)}")
        return keccak(bytes([KEYCHAIN_SIGNATURE_TYPE]) + sig_hash + bytes(as_address(user_address)))

    @property
    def user_address(self) -> Address:
        """The root account the access key signed on behalf of (20 bytes)."""
        return Address(self.raw[1:_INNER_OFFSET])

    @property
    def inner_signature(self) -> Signature:
        """The 65-byte inner secp256k1 signature from the access key."""
        return Signature.from_bytes(self.raw[_INNER_OFFSET:KEYCHAIN_SIGNATURE_LENGTH])

    def to_bytes(self) -> bytes:
        """Return the 86-byte blob (used as the transaction's sender signature)."""
        return self.raw


# ---------------------------------------------------------------------------
# Keychain signature construction
# ---------------------------------------------------------------------------


def build_keychain_signature(inner_sig: Signature, user_address: BytesLike) -> bytes:
    """Build an 86-byte Keychain V2 blob (``0x04 || root(20) || inner(65)``)."""
    return KeychainSignature.from_inner(inner_sig, user_address).to_bytes()


# ---------------------------------------------------------------------------
# Sign transaction with access key
# ---------------------------------------------------------------------------


def sign_tx_access_key(
    tx: TempoTransaction,
    access_key_sk: str,
    root_account: Signer,
    *,
    chain_id: int | None = None,
    key_type: SignatureType = SignatureType.SECP256K1,
    key_id: BytesLike | None = None,
    expiry: int = 0,
    limits: tuple[TokenLimit, ...] | None = None,
    allowed_calls: tuple[CallScope, ...] | None = None,
    is_admin: bool = True,
) -> TempoTransaction:
    """Sign a transaction with an access key, provisioning it inline.

    The *root_account* signs the ``KeyAuthorization`` (producing a
    ``SignedKeyAuthorization``) and the access key signs the transaction as a
    Keychain V2 signature, so the key is provisioned and used in one tx.

    Args:
        tx: Unsigned or partially-built transaction. Its ``key_authorization``
            and ``sender_signature`` will be replaced.
        access_key_sk: Hex-encoded private key of the access key (secp256k1).
        root_account: The root ``Signer`` that grants the authorization.
        chain_id: Chain ID (defaults to ``tx.chain_id``).
        key_type: Type of the access key (only SECP256K1 is supported).
        key_id: Access key address (defaults to signing key's address).
        expiry: Unix timestamp for expiry (0 = never; non-admin only).
        limits: Per-token spending limits (``None`` = unlimited, ``()`` =
            deny all spending; non-admin only).
        allowed_calls: Allowed call scopes (``None`` = unrestricted, ``()`` =
            deny all calls; non-admin only).
        is_admin: Whether the key has unrestricted admin access.

    Returns:
        A new ``TempoTransaction`` with ``key_authorization`` and
        ``sender_signature`` set.

    For more info (same-transaction provision-and-use validation):
    https://github.com/tempoxyz/tempo/blob/d0b4ca4/crates/revm/src/handler.rs#L1823
    """
    if key_type != SignatureType.SECP256K1:
        raise ValueError(f"only SECP256K1 access keys are supported, got {SignatureType(key_type).name}")

    access_key = Signer(access_key_sk)
    actual_chain_id = chain_id if chain_id is not None else tx.chain_id
    actual_key_id = as_address(key_id) if key_id is not None else access_key.address
    root_addr = as_address(root_account.address)

    auth = KeyAuthorization(
        chain_id=actual_chain_id,
        key_type=int(key_type),
        key_id=actual_key_id,
        expiry=expiry,
        limits=limits,
        allowed_calls=allowed_calls,
        is_admin=is_admin,
        account=root_addr,
    )

    # SignedKeyAuthorization = [authorization, root_sig]. The node re-encodes
    # embedded secp256k1 sigs with v in {27, 28} when recomputing hashes, so
    # commit to the canonical form (else same-tx key recovery mismatches).
    root_sig = root_account.sign(auth.signature_hash())
    tx_with_auth = tx._replace_fields(key_authorization=[auth.to_rlp(), root_sig.to_canonical_bytes()])

    # The access key signs the Keychain V2 domain-separated hash; the sender
    # signature is the 0x04 || root || inner envelope.
    inner_sig = access_key.sign(KeychainSignature.signing_hash(get_sign_payload(tx_with_auth), root_addr))

    return tx_with_auth._replace_fields(
        sender_signature=KeychainSignature.from_inner(inner_sig, root_addr),
        sender_address=root_addr,
    )
