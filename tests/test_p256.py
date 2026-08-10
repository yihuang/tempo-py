"""Tests for tempo.p256 -- P-256 and WebAuthn signature envelopes."""

from hashlib import sha256

import pytest
from eth_utils import keccak, to_checksum_address

from tempo.keychain import KEYCHAIN_SIGNATURE_TYPE, KeychainSignature
from tempo.models import Signature
from tempo.p256 import (
    AT,
    P256_HALF_N,
    P256_N,
    P256_SIGNATURE_TYPE,
    UP,
    UV,
    WEBAUTHN_SIGNATURE_TYPE,
    P256Signature,
    P256Signer,
    WebAuthnSignature,
    derive_address,
    webauthn_challenge,
    webauthn_message_hash,
)

HASH = keccak(b"a tempo transaction")
RP_ID = "wallet.example"


@pytest.fixture
def signer() -> P256Signer:
    return P256Signer.generate()


class TestP256Signer:
    def test_roundtrips_through_its_private_key(self, signer: P256Signer) -> None:
        assert P256Signer(signer.private_key).address == signer.address

    def test_address_is_keccak_of_the_public_key(self, signer: P256Signer) -> None:
        assert signer.address == keccak(signer.pub_key_x + signer.pub_key_y)[12:]
        assert len(signer.address) == 20
        assert signer.checksum_address == to_checksum_address(signer.address)

    def test_signature_verifies_and_carries_the_public_key(self, signer: P256Signer) -> None:
        sig = signer.sign(HASH)

        assert sig.verify(HASH)
        assert not sig.verify(keccak(b"another transaction"))
        assert sig.address == signer.address

    def test_signature_is_low_s(self, signer: P256Signer) -> None:
        # ECDSA nonces are random, so sign repeatedly rather than trusting one draw
        for _ in range(16):
            assert int.from_bytes(signer.sign(HASH).s, "big") <= P256_HALF_N

    def test_pre_hash_signs_the_hashed_payload(self, signer: P256Signer) -> None:
        sig = signer.sign(HASH, pre_hash=True)

        assert sig.pre_hash
        # verify applies pre_hash the way the node does, so both forms answer for HASH itself
        assert sig.verify(HASH)
        assert not sig.verify(sha256(HASH).digest())
        # the underlying signature really is over the pre-hashed payload
        assert P256Signature(sig.r, sig.s, sig.pub_key_x, sig.pub_key_y).verify(sha256(HASH).digest())

    @pytest.mark.parametrize("pre_hash", [False, True], ids=["raw", "prehash"])
    def test_rejects_a_hash_that_is_not_32_bytes(self, signer: P256Signer, pre_hash: bool) -> None:
        with pytest.raises(ValueError, match="32 bytes"):
            signer.sign(b"\x01" * 31, pre_hash=pre_hash)


class TestP256Signature:
    def test_envelope_layout(self, signer: P256Signer) -> None:
        sig = signer.sign(HASH)
        raw = sig.to_bytes()

        assert len(raw) == 130
        assert raw[0] == P256_SIGNATURE_TYPE
        assert raw[1:33] == sig.r
        assert raw[33:65] == sig.s
        assert raw[65:97] == signer.pub_key_x
        assert raw[97:129] == signer.pub_key_y
        assert raw[129] == 0
        assert signer.sign(HASH, pre_hash=True).to_bytes()[129] == 1

    def test_rejects_high_s(self, signer: P256Signer) -> None:
        sig = signer.sign(HASH)
        high_s = (P256_N - int.from_bytes(sig.s, "big")).to_bytes(32, "big")

        with pytest.raises(ValueError, match="low-s"):
            P256Signature(sig.r, high_s, sig.pub_key_x, sig.pub_key_y)

    def test_rejects_a_scalar_out_of_range(self, signer: P256Signer) -> None:
        sig = signer.sign(HASH)
        with pytest.raises(ValueError, match=r"r must be in \(0, p256_n\)"):
            P256Signature(bytes(32), sig.s, sig.pub_key_x, sig.pub_key_y)


class TestWebAuthnSignature:
    def test_assertion_signs_over_its_own_data(self, signer: P256Signer) -> None:
        sig = signer.sign_webauthn(HASH, rp_id=RP_ID)

        assert sig.verify()
        assert sig.message_hash == webauthn_message_hash(sig.authenticator_data, sig.client_data_json)
        assert sig.address == signer.address

    def test_client_data_binds_the_transaction_hash(self, signer: P256Signer) -> None:
        sig = signer.sign_webauthn(HASH, rp_id=RP_ID)
        client_data = sig.client_data_json.decode()

        assert '"type":"webauthn.get"' in client_data
        assert webauthn_challenge(HASH) in client_data
        assert f'"origin":"https://{RP_ID}"' in client_data

    def test_envelope_layout(self, signer: P256Signer) -> None:
        sig = signer.sign_webauthn(HASH, rp_id=RP_ID)
        raw = sig.to_bytes()

        assert raw[0] == WEBAUTHN_SIGNATURE_TYPE
        assert raw[1:38] == sig.authenticator_data
        assert raw[38 : 38 + len(sig.client_data_json)] == sig.client_data_json
        assert raw[-128:] == sig.r + sig.s + signer.pub_key_x + signer.pub_key_y

    def test_flags_reach_authenticator_data(self, signer: P256Signer) -> None:
        assert signer.sign_webauthn(HASH, rp_id=RP_ID).authenticator_data[32] == UP | UV
        assert signer.sign_webauthn(HASH, rp_id=RP_ID, flags=UV).authenticator_data[32] == UV

    def test_rejects_authenticator_data_of_the_wrong_length(self, signer: P256Signer) -> None:
        sig = signer.sign_webauthn(HASH, rp_id=RP_ID)
        attested = sig.authenticator_data[:32] + bytes([UV | AT]) + sig.authenticator_data[33:] + b"\x00" * 16

        with pytest.raises(ValueError, match="authenticator_data must be 37 bytes"):
            WebAuthnSignature(sig.r, sig.s, sig.pub_key_x, sig.pub_key_y, attested, sig.client_data_json)


class TestDeriveAddress:
    def test_matches_the_signer(self, signer: P256Signer) -> None:
        assert derive_address(signer.pub_key_x, signer.pub_key_y) == signer.address

    def test_rejects_a_key_of_the_wrong_length(self) -> None:
        with pytest.raises(ValueError, match="two 32-byte coordinates"):
            derive_address(bytes(32), bytes(31))


class TestKeychainWithP256Inner:
    """A passkey registered as an access key of an ordinary account."""

    ROOT = "0x" + "11" * 20

    def test_wraps_a_p256_envelope(self, signer: P256Signer) -> None:
        inner = signer.sign(KeychainSignature.signing_hash(HASH, self.ROOT))
        blob = KeychainSignature.from_inner(inner, self.ROOT)

        assert blob.to_bytes()[0] == KEYCHAIN_SIGNATURE_TYPE
        assert blob.user_address == bytes.fromhex(self.ROOT[2:])
        assert blob.inner_bytes == inner.to_bytes()
        assert len(blob.to_bytes()) == 1 + 20 + 130

    def test_wraps_a_webauthn_envelope(self, signer: P256Signer) -> None:
        inner = signer.sign_webauthn(KeychainSignature.signing_hash(HASH, self.ROOT), rp_id=RP_ID)
        blob = KeychainSignature.from_inner(inner, self.ROOT)

        assert blob.inner_bytes == inner.to_bytes()
        assert inner.verify()

    def test_inner_signature_still_reads_a_secp256k1_inner(self) -> None:
        secp = Signature(r=1, s=2, v=27)
        assert KeychainSignature.from_inner(secp, self.ROOT).inner_signature == secp

    def test_inner_signature_refuses_a_p256_inner(self, signer: P256Signer) -> None:
        blob = KeychainSignature.from_inner(signer.sign(HASH), self.ROOT)
        with pytest.raises(ValueError, match="not secp256k1"):
            blob.inner_signature

    def test_rejects_a_blob_with_no_inner(self) -> None:
        with pytest.raises(ValueError, match="must carry an inner signature"):
            KeychainSignature(raw=bytes([KEYCHAIN_SIGNATURE_TYPE]) + bytes(20))
