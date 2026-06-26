"""ECDSA key management and signature generation / recovery.

The Signer wraps a secp256k1 private key and computes, recovers, and verifies
signatures.  ``recover_address`` is a pure function usable without a Signer
instance.
"""

from __future__ import annotations

from eth_account import Account
from eth_utils import keccak, to_bytes, to_checksum_address

from .models import SECP256K1_HALF_N, SECP256K1_N, Signature
from .types import Address, BytesLike, as_address


class Signer:
    """Wrapper for managing an ECDSA private key and signing hashes."""

    def __init__(self, private_key_hex: str) -> None:
        self._account = Account.from_key(private_key_hex)

    @property
    def address(self) -> Address:
        return Address(to_bytes(hexstr=self._account.address))

    @property
    def checksum_address(self) -> str:
        return self._account.address

    @property
    def private_key(self) -> str:
        return self._account.key.hex()

    def sign(self, message_hash: bytes) -> Signature:
        """Sign a 32-byte hash with the signer's private key.

        Returns:
            A validated Signature with low-s canonical form.
        """
        signed = self._account._key_obj.sign_msg_hash(message_hash)
        # eth_account returns (v, r, s) with v in {0, 1}.
        sig = Signature(r=signed.r, s=signed.s, v=signed.v)
        return sig

    def sign_data(self, data: bytes) -> Signature:
        """Hash arbitrary data with keccak256 and sign."""
        return self.sign(keccak(data))

    def verify_signature(self, message_hash: bytes, sig: Signature) -> bool:
        """Check whether *sig* was created by this signer over *message_hash*."""
        recovered = recover_address(message_hash, sig)
        return recovered == self.address


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def recover_address(message_hash: bytes, sig: Signature) -> Address:
    """Recover the address that produced *sig* over *message_hash*.

    Validates:
    - r and s are within valid secp256k1 bounds
    - s is in low-s canonical form (EIP-2)
    - v is 0, 1, 27, or 28
    """
    if sig.r <= 0 or sig.r >= SECP256K1_N:
        raise ValueError(f"r out of range: {sig.r}")
    if sig.s <= 0 or sig.s > SECP256K1_HALF_N:
        raise ValueError(f"s out of range (expected low-s): {sig.s}")
    if sig.v not in (0, 1, 27, 28):
        raise ValueError(f"v must be 0, 1, 27, or 28: {sig.v}")

    r_bytes = sig.r.to_bytes(32, "big")
    s_bytes = sig.s.to_bytes(32, "big")
    v_bytes = bytes([sig.v])

    sig_bytes = r_bytes + s_bytes + v_bytes
    recovered = Account._recover_hash(message_hash, signature=sig_bytes)
    return Address(to_bytes(hexstr=recovered))


def verify_signature(message_hash: bytes, sig: Signature, address: BytesLike) -> bool:
    """Check that *sig* is valid for *message_hash* and recovers to *address*."""
    return recover_address(message_hash, sig) == as_address(address)
