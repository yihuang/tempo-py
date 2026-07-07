"""Tempo Transaction helpers: RLP serialization, signing, TIP-20 encoding, Builder."""

from __future__ import annotations

from typing import Optional

import attrs
import rlp
from eth_utils import keccak, to_bytes

from .constants import DEFAULT_CHAIN_ID, DEFAULT_NONCE_KEY
from .models import AccessListItem, Call, Signature, TempoTransaction
from .signer import Signer, recover_address
from .types import Address, BytesLike, as_address, as_optional_address


def parse_topic_address(topic: str) -> Address:
    """Extract the address from an indexed event topic hex string."""
    raw = topic.removeprefix("0x")
    if len(raw) != 64:
        return Address(b"")
    topic_bytes = bytes.fromhex(raw)
    return Address(topic_bytes[12:32])


# ---------------------------------------------------------------------------
# RLP serialization helpers
# ---------------------------------------------------------------------------


def _int_to_rlp_bytes(value: int) -> bytes:
    """Convert an integer to RLP-friendly bytes (empty if zero)."""
    if value == 0:
        return b""
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def _address_to_rlp_bytes(addr: Address) -> bytes:
    """Convert address to RLP bytes (empty if zero address)."""
    raw = bytes(addr)
    if raw == b"\x00" * 20:
        return b""
    return raw


def _bigint_to_rlp_bytes(value: int) -> bytes:
    """Convert big int to bytes for RLP encoding."""
    return _int_to_rlp_bytes(value)


def _encode_calls(calls: tuple[Call, ...]) -> list:
    """Encode calls as RLP list of [to, value, data]."""
    result = []
    for c in calls:
        to_bytes_val = bytes(c.to) if c.to else b""
        val_bytes = _int_to_rlp_bytes(c.value)
        data_bytes = c.data or b""
        result.append([to_bytes_val, val_bytes, data_bytes])
    return result


def _encode_access_list(
    access_list: tuple[object, ...],
) -> list:
    """Encode access list as RLP list of [address, [storageKeys]]."""
    result = []
    for item in access_list:
        if isinstance(item, AccessListItem):
            addr_bytes = bytes(item.address)
            keys = [bytes(k) for k in item.storage_keys]
            result.append([addr_bytes, keys])
        else:
            result.append([b"", []])
    return result


def _encode_signature(sig: Optional[Signature]) -> bytes:
    """Encode a secp256k1 Signature as 65 raw bytes (r || s || v) for the envelope."""
    if sig is None:
        return b""
    return sig.to_bytes()


def _encode_fee_payer_field(tx: TempoTransaction) -> object:
    """Encode field 11 (feePayerSignatureOrSender).

    - If fee_payer_signature is present: [yParity, r, s]
    - If awaiting_fee_payer: 0x00 marker
    - Otherwise: empty bytes
    """
    if tx.fee_payer_signature is not None:
        return tx.fee_payer_signature.to_rlp_list()
    if tx.awaiting_fee_payer:
        return b"\x00"
    return b""


def _encode_fee_token(tx: TempoTransaction, skip: bool) -> bytes:
    """Encode field 10 (feeToken), or empty (0x80) when skipped."""
    if skip or tx.fee_token is None:
        return b""
    return bytes(tx.fee_token)


# ---------------------------------------------------------------------------
# Serialize
# ---------------------------------------------------------------------------


def serialize_for_signing(tx: TempoTransaction) -> str:
    """Serialize a transaction for sender signing (no signatures).

    Per Tempo spec:
    - fee_token is SKIPPED when fee payer is involved
    - feePayerSignatureOrSender uses 0x00 marker if awaiting_fee_payer
    - All signatures are removed
    """
    # Skipped once a fee payer is involved so the sender never commits to a
    # fee token. Upstream keys this on fee_payer_signature.is_some():
    # https://github.com/tempoxyz/tempo/blob/d0b4ca4/crates/primitives/src/transaction/tempo_transaction.rs#L755
    skip_fee_token = tx.awaiting_fee_payer or tx.fee_payer_signature is not None
    fields = _build_rlp_fields(tx, skip_fee_token=skip_fee_token, drop_signatures=True)
    encoded = rlp.encode(fields)
    return "0x76" + encoded.hex()


def serialize_for_fee_payer_signing(tx: TempoTransaction, sender: BytesLike) -> str:
    """Serialize for fee payer signing (0x78 prefix).

    Strips BOTH sender and fee payer signatures, encodes sender address
    in the feePayerSignatureOrSender field. The fee payer commits to fee_token,
    so it is never skipped here (unlike the sender's payload):
    https://github.com/tempoxyz/tempo/blob/d0b4ca4/crates/primitives/src/transaction/tempo_transaction.rs#L401
    """
    fields = _build_rlp_fields(tx, skip_fee_token=False, drop_signatures=True)
    fields[11] = bytes(as_address(sender))
    encoded = rlp.encode(fields)
    return "0x78" + encoded.hex()


def serialize(tx: TempoTransaction) -> str:
    """Serialize a fully-signed transaction for broadcast (0x76 prefix)."""
    fields = _build_rlp_fields(tx, skip_fee_token=False, drop_signatures=False)
    encoded = rlp.encode(fields)
    return "0x76" + encoded.hex()


def _build_rlp_fields(
    tx: TempoTransaction,
    skip_fee_token: bool,
    drop_signatures: bool,
) -> list:
    """Build the RLP field list for a TempoTransaction.

    Field layout (13-15 fields):
    0:  chainId
    1:  maxPriorityFeePerGas
    2:  maxFeePerGas
    3:  gas
    4:  calls (array of [to, value, data])
    5:  accessList
    6:  nonceKey
    7:  nonce
    8:  validBefore
    9:  validAfter
    10: feeToken
    11: feePayerSignatureOrSender
    12: authorizationList (reserved)
    13: keyAuthorization (optional) OR signatureEnvelope (optional)
    14: signatureEnvelope (when field 13 is keyAuthorization)
    """
    fields: list[object] = [
        _bigint_to_rlp_bytes(tx.chain_id),
        _int_to_rlp_bytes(tx.max_priority_fee_per_gas),
        _int_to_rlp_bytes(tx.max_fee_per_gas),
        _int_to_rlp_bytes(tx.gas_limit),
        _encode_calls(tx.calls),
        _encode_access_list(tx.access_list),
        _bigint_to_rlp_bytes(tx.nonce_key),
        _int_to_rlp_bytes(tx.nonce),
        _int_to_rlp_bytes(tx.valid_before or 0),
        _int_to_rlp_bytes(tx.valid_after or 0),
        _encode_fee_token(tx, skip_fee_token),
        _encode_fee_payer_field(tx),
        [],  # authorizationList (reserved, empty)
    ]

    # Key authorization
    if tx.key_authorization is not None:
        fields.append(tx.key_authorization)

    # Sender signature envelope
    if not drop_signatures and tx.sender_signature is not None:
        fields.append(_encode_signature(tx.sender_signature))

    return fields


# ---------------------------------------------------------------------------
# Deserialize
# ---------------------------------------------------------------------------


def compute_hash(serialized_hex: str) -> bytes:
    """Compute keccak256 hash of a serialized transaction hex string."""
    raw = to_bytes(hexstr=serialized_hex)
    return keccak(raw)


def get_sign_payload(tx: TempoTransaction) -> bytes:
    """Compute the hash the sender should sign."""
    ser = serialize_for_signing(tx)
    return compute_hash(ser)


def get_fee_payer_sign_payload(tx: TempoTransaction, sender: BytesLike) -> bytes:
    """Compute the hash the fee payer should sign."""
    ser = serialize_for_fee_payer_signing(tx, sender)
    return compute_hash(ser)


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def sign_transaction(tx: TempoTransaction, signer: Signer) -> TempoTransaction:
    """Sign a transaction with the sender's private key.

    Returns a new TempoTransaction with the sender signature attached.
    The original transaction is not modified.
    """
    hash_to_sign = get_sign_payload(tx)
    sig = signer.sign(hash_to_sign)
    return tx._replace_fields(
        sender_signature=sig,
        sender_address=signer.address,
    )


def add_fee_payer_signature(tx: TempoTransaction, fee_payer: Signer) -> TempoTransaction:
    """Add a fee payer signature to an already-sender-signed transaction.

    Returns a new TempoTransaction with the fee payer signature attached.
    """
    if tx.sender_signature is None:
        raise ValueError("transaction must have sender signature before adding fee payer signature")

    sender = tx.sender_address
    hash_to_sign = get_fee_payer_sign_payload(tx, sender)
    sig = fee_payer.sign(hash_to_sign)
    return tx._replace_fields(fee_payer_signature=sig)


def verify_signature(tx: TempoTransaction) -> Address:
    """Recover and return the sender address from a signed transaction.

    For a keychain-signed transaction the inner signature is recovered over
    the Keychain V2 domain-separated hash; the recovered key must be the
    access key, and the returned sender is the root account it signed for.

    Raises:
        ValueError: If transaction has no signature or recovery fails.
    """
    if tx.sender_signature is None:
        raise ValueError("transaction has no sender signature")

    # Inline import avoids circular dep: keychain -> transaction -> keychain
    from .keychain import KeychainSignature  # noqa: PLC0415

    hash_signed = get_sign_payload(tx)
    sig = tx.sender_signature
    if isinstance(sig, KeychainSignature):
        # Recovery validates the inner signature; the tx sender is the root.
        recover_address(KeychainSignature.signing_hash(hash_signed, sig.user_address), sig.inner_signature)
        return sig.user_address
    return recover_address(hash_signed, sig)


def verify_fee_payer_signature(tx: TempoTransaction, sender: BytesLike) -> Address:
    """Recover and return the fee payer address from a signed transaction."""
    if tx.fee_payer_signature is None:
        raise ValueError("transaction has no fee payer signature")

    hash_signed = get_fee_payer_sign_payload(tx, sender)
    return recover_address(hash_signed, tx.fee_payer_signature)


# ---------------------------------------------------------------------------
# TempoTransaction._replace_fields (monkey-patch to avoid circular import)
# ---------------------------------------------------------------------------


def _replace_fields(
    tx: TempoTransaction,
    *,
    sender_signature: Optional[Signature] = None,
    fee_payer_signature: Optional[Signature] = None,
    sender_address: Optional[Address] = None,
    awaiting_fee_payer: Optional[bool] = None,
    **kwargs: object,
) -> TempoTransaction:
    """Return a new TempoTransaction with the given fields replaced."""
    kw: dict[str, object] = {}
    if sender_signature is not None:
        kw["sender_signature"] = sender_signature
    if fee_payer_signature is not None:
        kw["fee_payer_signature"] = fee_payer_signature
    if sender_address is not None:
        kw["sender_address"] = sender_address
    if awaiting_fee_payer is not None:
        kw["awaiting_fee_payer"] = awaiting_fee_payer
    kw.update(kwargs)
    return attrs.evolve(tx, **kw)


# Apply the monkey-patch to the model class
TempoTransaction._replace_fields = _replace_fields  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class Builder:
    """Fluent builder for constructing TempoTransaction objects.

    Example::

        tx = (Builder()
              .chain_id(CHAIN_ID_MODERATO)
              .gas_limit(100_000)
              .max_fee_per_gas(2_000_000_000)
              .add_call(to=recipient, value=0, data=data)
              .build())
    """

    def __init__(self) -> None:
        self._chain_id = DEFAULT_CHAIN_ID
        self._max_priority_fee = 0
        self._max_fee = 0
        self._gas_limit = 21_000
        self._calls: list[Call] = []
        self._access_list: list[object] = []
        self._nonce_key = DEFAULT_NONCE_KEY
        self._nonce = 0
        self._valid_before: Optional[int] = None
        self._valid_after: Optional[int] = None
        self._fee_token: Optional[Address] = None
        self._awaiting_fee_payer = False

    def chain_id(self, cid: int) -> Builder:
        self._chain_id = cid
        return self

    def max_priority_fee_per_gas(self, fee: int) -> Builder:
        self._max_priority_fee = fee
        return self

    def max_fee_per_gas(self, fee: int) -> Builder:
        self._max_fee = fee
        return self

    def gas_limit(self, gas: int) -> Builder:
        self._gas_limit = gas
        return self

    def nonce(self, n: int) -> Builder:
        self._nonce = n
        return self

    def nonce_key(self, key: int) -> Builder:
        self._nonce_key = key
        return self

    def valid_before(self, ts: int) -> Builder:
        self._valid_before = ts
        return self

    def valid_after(self, ts: int) -> Builder:
        self._valid_after = ts
        return self

    def fee_token(self, token: Optional[BytesLike]) -> Builder:
        self._fee_token = as_optional_address(token) if token is not None else None
        return self

    def awaiting_fee_payer(self, val: bool = True) -> Builder:
        self._awaiting_fee_payer = val
        return self

    def add_call(
        self,
        to: BytesLike,
        value: int = 0,
        data: BytesLike = b"",
    ) -> Builder:
        self._calls.append(Call.create(to=to, value=value, data=data))
        return self

    def add_access_list_entry(
        self,
        address: BytesLike,
        storage_keys: tuple[BytesLike, ...] = (),
    ) -> Builder:
        self._access_list.append(AccessListItem.create(address=address, storage_keys=storage_keys))
        return self

    def build(self) -> TempoTransaction:
        """Build and return the transaction (no validation)."""
        return TempoTransaction.create(
            chain_id=self._chain_id,
            gas_limit=self._gas_limit,
            max_fee_per_gas=self._max_fee,
            max_priority_fee_per_gas=self._max_priority_fee,
            nonce=self._nonce,
            nonce_key=self._nonce_key,
            valid_before=self._valid_before,
            valid_after=self._valid_after,
            fee_token=self._fee_token,
            awaiting_fee_payer=self._awaiting_fee_payer,
            calls=tuple(self._calls),
            access_list=tuple(self._access_list),
        )
