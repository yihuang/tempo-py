"""P-256 (secp256r1) and WebAuthn signatures.

A Tempo account can be keyed by a P-256 public key instead of secp256k1; its address is
``keccak256(pubKeyX || pubKeyY)[12:]``, so there is no private key for the owner to hold.
A WebAuthn (passkey) credential is the same curve, signing over an authenticator
assertion rather than over the transaction hash directly.

Envelope layouts, as the node's ``PrimitiveSignature::to_bytes`` emits them::

    P256:     0x01 || r || s || pubKeyX || pubKeyY || preHash
    WebAuthn: 0x02 || authenticatorData || clientDataJSON || r || s || pubKeyX || pubKeyY

Both carry the public key, so the node derives the sender from the signature itself.

For more info (signature parsing and recovery):
https://github.com/tempoxyz/tempo/blob/db8e7ab/crates/primitives/src/transaction/tt_signature.rs
"""

from __future__ import annotations

import json
from base64 import urlsafe_b64encode
from hashlib import sha256
from types import SimpleNamespace

import attrs
from Crypto.PublicKey import ECC
from Crypto.Signature import DSS
from eth_utils import keccak, to_checksum_address

from .types import Address, BytesLike, as_bytes

# Envelope type bytes.
P256_SIGNATURE_TYPE = 0x01
WEBAUTHN_SIGNATURE_TYPE = 0x02

# secp256r1 group order. Signatures must be low-s; the node rejects the malleable twin.
P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
P256_HALF_N = P256_N // 2

# authenticatorData flags (byte 32). Tempo needs UP or UV set, and rejects AT and ED.
UP, UV, AT, ED = 0x01, 0x04, 0x40, 0x80

_AUTH_DATA_LENGTH = 37  # sha256(rpId)(32) || flags(1) || signCount(4)


def _as_hash_object(digest: bytes) -> SimpleNamespace:
    """Present a ready-made digest as the hash object DSS expects.

    A signing payload is a keccak hash, not the SHA-256 of anything, so there is no real
    hash object to hand over. DSS reads only ``digest()`` and ``oid`` (a strength check).
    """
    return SimpleNamespace(digest=lambda: digest, oid="2.16.840.1.101.3.4.2.1")  # SHA-256


def derive_address(pub_key_x: BytesLike, pub_key_y: BytesLike) -> Address:
    """The account address of a P-256 / WebAuthn public key: ``keccak256(x || y)[12:]``."""
    coords = as_bytes(pub_key_x) + as_bytes(pub_key_y)
    if len(coords) != 64:
        raise ValueError(f"public key must be two 32-byte coordinates, got {len(coords)} bytes")
    return Address(keccak(coords)[12:])


def webauthn_challenge(sig_hash: BytesLike) -> str:
    """The transaction hash as a WebAuthn challenge (base64url, unpadded)."""
    return urlsafe_b64encode(as_bytes(sig_hash)).rstrip(b"=").decode()


def webauthn_message_hash(authenticator_data: BytesLike, client_data_json: BytesLike) -> bytes:
    """``sha256(authenticatorData || sha256(clientDataJSON))`` -- what an authenticator signs."""
    return sha256(as_bytes(authenticator_data) + sha256(as_bytes(client_data_json)).digest()).digest()


def build_authenticator_data(rp_id: str, *, flags: int = UP | UV, sign_count: int = 0) -> bytes:
    """``sha256(rpId) || flags || signCount``, as an authenticator emits it for an assertion."""
    return sha256(rp_id.encode()).digest() + bytes([flags]) + sign_count.to_bytes(4, "big")


def build_client_data_json(sig_hash: BytesLike, origin: str, *, type_field: str = "webauthn.get") -> bytes:
    """The clientDataJSON a browser builds for ``navigator.credentials.get()``."""
    fields = {
        "type": type_field,
        "challenge": webauthn_challenge(sig_hash),
        "origin": origin,
        "crossOrigin": False,
    }
    return json.dumps(fields, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------


def _validate_coordinate(instance: object, attribute: attrs.Attribute, value: bytes) -> None:
    if len(value) != 32:
        raise ValueError(f"{attribute.name} must be 32 bytes, got {len(value)}")


def _validate_scalar(instance: object, attribute: attrs.Attribute, value: bytes) -> None:
    _validate_coordinate(instance, attribute, value)
    number = int.from_bytes(value, "big")
    if not 0 < number < P256_N:
        raise ValueError(f"{attribute.name} must be in (0, p256_n), got {number}")


def _validate_low_s(instance: object, attribute: attrs.Attribute, value: bytes) -> None:
    _validate_scalar(instance, attribute, value)
    number = int.from_bytes(value, "big")
    if number > P256_HALF_N:
        raise ValueError(f"s must be in low-s canonical form: (0, p256_n/2], got {number}")


def _validate_authenticator_data(instance: object, attribute: attrs.Attribute, value: bytes) -> None:
    if len(value) != _AUTH_DATA_LENGTH:
        raise ValueError(
            f"authenticator_data must be {_AUTH_DATA_LENGTH} bytes "
            f"(no attested credential or extension data), got {len(value)}"
        )


# ---------------------------------------------------------------------------
# Signature envelopes
# ---------------------------------------------------------------------------


@attrs.define(frozen=True)
class _P256Envelope:
    """What every P-256 signature carries: the signature, and the key that made it."""

    r: bytes = attrs.field(converter=as_bytes, validator=_validate_scalar)
    s: bytes = attrs.field(converter=as_bytes, validator=_validate_low_s)
    pub_key_x: bytes = attrs.field(converter=as_bytes, validator=_validate_coordinate)
    pub_key_y: bytes = attrs.field(converter=as_bytes, validator=_validate_coordinate)

    @property
    def address(self) -> Address:
        """The account this signature recovers to."""
        return derive_address(self.pub_key_x, self.pub_key_y)

    def verify(self, message_hash: BytesLike) -> bool:
        """Check the signature against its own embedded public key."""
        key = ECC.construct(
            curve="P-256",
            point_x=int.from_bytes(self.pub_key_x, "big"),
            point_y=int.from_bytes(self.pub_key_y, "big"),
        )
        try:
            DSS.new(key, "fips-186-3").verify(_as_hash_object(as_bytes(message_hash)), self.r + self.s)
        except ValueError:
            return False
        return True


@attrs.define(frozen=True)
class P256Signature(_P256Envelope):
    """A P-256 signature over a transaction hash (130 bytes with the type byte).

    Attributes:
        pre_hash: The signature is over ``sha256(sig_hash)`` rather than the transaction
            hash itself, for signers (WebCrypto) that will not sign a bare digest.
    """

    pre_hash: bool = False

    def verify(self, message_hash: BytesLike) -> bool:
        """Check the signature against ``message_hash``, applying ``pre_hash`` as the node does."""
        payload = as_bytes(message_hash)
        return super().verify(sha256(payload).digest() if self.pre_hash else payload)

    def to_bytes(self) -> bytes:
        """``0x01 || r || s || pubKeyX || pubKeyY || preHash``."""
        prefix = bytes([P256_SIGNATURE_TYPE])
        return prefix + self.r + self.s + self.pub_key_x + self.pub_key_y + bytes([int(self.pre_hash)])


@attrs.define(frozen=True)
class WebAuthnSignature(_P256Envelope):
    """A passkey assertion: the signature plus the data the authenticator signed over.

    The node re-checks that ``client_data_json`` is a ``webauthn.get`` whose challenge is
    the transaction hash, so the assertion is what binds the passkey to this transaction.
    """

    authenticator_data: bytes = attrs.field(converter=as_bytes, validator=_validate_authenticator_data)
    client_data_json: bytes = attrs.field(converter=as_bytes)

    @property
    def message_hash(self) -> bytes:
        """What the authenticator actually signed."""
        return webauthn_message_hash(self.authenticator_data, self.client_data_json)

    def verify(self, message_hash: BytesLike | None = None) -> bool:
        """Check the assertion; defaults to the hash it says it signed."""
        return super().verify(self.message_hash if message_hash is None else message_hash)

    def to_bytes(self) -> bytes:
        """``0x02 || authenticatorData || clientDataJSON || r || s || pubKeyX || pubKeyY``."""
        prefix = bytes([WEBAUTHN_SIGNATURE_TYPE]) + self.authenticator_data + self.client_data_json
        return prefix + self.r + self.s + self.pub_key_x + self.pub_key_y


# ---------------------------------------------------------------------------
# Signer
# ---------------------------------------------------------------------------


class P256Signer:
    """Manages a P-256 private key and signs hashes, mirroring `Signer` for secp256k1."""

    def __init__(self, private_key_hex: str) -> None:
        self._key = ECC.construct(curve="P-256", d=int(private_key_hex, 16))
        self.pub_key_x = int(self._key.pointQ.x).to_bytes(32, "big")
        self.pub_key_y = int(self._key.pointQ.y).to_bytes(32, "big")

    @classmethod
    def generate(cls) -> P256Signer:
        """A signer over a freshly generated keypair."""
        return cls(f"0x{int(ECC.generate(curve='P-256').d):064x}")

    @property
    def private_key(self) -> str:
        return f"0x{int(self._key.d):064x}"

    @property
    def address(self) -> Address:
        """``keccak256(pubKeyX || pubKeyY)[12:]``."""
        return derive_address(self.pub_key_x, self.pub_key_y)

    @property
    def checksum_address(self) -> str:
        return to_checksum_address(self.address)

    def sign(self, message_hash: BytesLike, *, pre_hash: bool = False) -> P256Signature:
        """Sign a 32-byte hash, returning a low-s `P256Signature`."""
        payload = as_bytes(message_hash)
        if len(payload) != 32:  # checked before pre-hashing, which would mask any length
            raise ValueError(f"message hash must be 32 bytes, got {len(payload)}")
        r, s = self._sign_raw(sha256(payload).digest() if pre_hash else payload)
        return P256Signature(r, s, self.pub_key_x, self.pub_key_y, pre_hash)

    def sign_webauthn(
        self,
        message_hash: BytesLike,
        *,
        rp_id: str,
        origin: str | None = None,
        flags: int = UP | UV,
        sign_count: int = 0,
    ) -> WebAuthnSignature:
        """Sign as a software stand-in for an authenticator.

        A real passkey returns ``authenticatorData``, ``clientDataJSON``, and the signature
        from the device; pass those to `WebAuthnSignature` directly. This assembles the
        same assertion locally, for tests and devnet tooling.
        """
        authenticator_data = build_authenticator_data(rp_id, flags=flags, sign_count=sign_count)
        client_data_json = build_client_data_json(message_hash, origin or f"https://{rp_id}")
        r, s = self._sign_raw(webauthn_message_hash(authenticator_data, client_data_json))
        return WebAuthnSignature(r, s, self.pub_key_x, self.pub_key_y, authenticator_data, client_data_json)

    def _sign_raw(self, message_hash: bytes) -> tuple[bytes, bytes]:
        """``(r, s)`` over a 32-byte hash, with ``s`` normalized to low-s."""
        raw = DSS.new(self._key, "fips-186-3").sign(_as_hash_object(message_hash))
        s = int.from_bytes(raw[32:], "big")
        return raw[:32], min(s, P256_N - s).to_bytes(32, "big")
