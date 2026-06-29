"""Tests for tempo.keychain — access key models and key authorization."""

from tempo.constants import ALPHA_USD
from tempo.keychain import (
    CallScope,
    KeyAuthorization,
    KeychainSignature,
    KeyRestrictions,
    SelectorRule,
    SignatureType,
    TokenLimit,
    build_keychain_signature,
)
from tempo.models import Signature

RECIPIENT = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
KEY_ID = "0xaaaaaaaa00000000000000000000000000000000"
ACCOUNT = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


# ---------------------------------------------------------------------------
# SignatureType
# ---------------------------------------------------------------------------


class TestSignatureType:
    def test_values(self):
        assert SignatureType.SECP256K1 == 0
        assert SignatureType.P256 == 1
        assert SignatureType.WEBAUTHN == 2

    def test_int_enum(self):
        assert int(SignatureType.SECP256K1) == 0


# ---------------------------------------------------------------------------
# TokenLimit
# ---------------------------------------------------------------------------


class TestTokenLimit:
    def test_create_basic(self):
        tl = TokenLimit(token=ALPHA_USD, limit=10**18)
        assert tl.limit == 10**18
        assert tl.period == 0

    def test_create_with_period(self):
        tl = TokenLimit(token=ALPHA_USD, limit=10**18, period=86400)
        assert tl.period == 86400

    def test_to_rlp(self):
        tl = TokenLimit(token=ALPHA_USD, limit=10**18)
        rlp = tl.to_rlp()
        assert len(rlp) == 3
        assert rlp[0] == bytes.fromhex(ALPHA_USD[2:])


# ---------------------------------------------------------------------------
# SelectorRule
# ---------------------------------------------------------------------------


class TestSelectorRule:
    def test_create(self):
        sr = SelectorRule.create(selector=bytes.fromhex("a9059cbb"))
        assert len(sr.selector) == 4
        assert sr.recipients == ()

    def test_create_with_recipients(self):
        sr = SelectorRule.create(
            selector=bytes.fromhex("a9059cbb"),
            recipients=(RECIPIENT,),
        )
        assert len(sr.recipients) == 1

    def test_to_rlp(self):
        sr = SelectorRule.create(selector=bytes.fromhex("a9059cbb"))
        rlp = sr.to_rlp()
        assert len(rlp) == 2


# ---------------------------------------------------------------------------
# CallScope
# ---------------------------------------------------------------------------


class TestCallScope:
    def test_transfer(self):
        cs = CallScope.transfer(ALPHA_USD)
        assert cs.selector == bytes.fromhex("a9059cbb")
        assert cs.target is not None

    def test_approve(self):
        cs = CallScope.approve(ALPHA_USD)
        assert cs.selector == bytes.fromhex("095ea7b3")

    def test_unrestricted(self):
        cs = CallScope.unrestricted(target=RECIPIENT)
        assert cs.selector == bytes(4)

    def test_with_selector(self):
        cs = CallScope.with_selector(
            target=RECIPIENT,
            selector=bytes.fromhex("a9059cbb"),
        )
        assert cs.selector == bytes.fromhex("a9059cbb")


# ---------------------------------------------------------------------------
# KeyRestrictions
# ---------------------------------------------------------------------------


class TestKeyRestrictions:
    def test_create_default(self):
        kr = KeyRestrictions.create(expiry=0)
        assert kr.expiry == 0
        assert not kr.enforce_limits
        assert kr.limits == ()

    def test_with_limits(self):
        kr = KeyRestrictions.create(expiry=0)
        kr2 = kr.with_limits(TokenLimit(token=ALPHA_USD, limit=10**18))
        assert kr2.enforce_limits
        assert len(kr2.limits) == 1

    def test_add_call(self):
        kr = KeyRestrictions.create(expiry=0)
        cs = CallScope.transfer(ALPHA_USD)
        kr2 = kr.add_call(cs)
        assert not kr2.allow_any_calls
        assert len(kr2.allowed_calls) == 1

    def test_to_rlp(self):
        kr = KeyRestrictions.create(expiry=0)
        rlp = kr.to_rlp()
        assert isinstance(rlp, list)
        assert len(rlp) == 5


# ---------------------------------------------------------------------------
# KeyAuthorization
# ---------------------------------------------------------------------------


class TestKeyAuthorization:
    def test_create_minimal(self):
        ka = KeyAuthorization(
            key_id=KEY_ID,
            account=ACCOUNT,
            chain_id=42431,
            key_type=SignatureType.SECP256K1,
        )
        assert ka.key_id is not None
        assert ka.account is not None
        assert ka.chain_id == 42431

    def test_create_with_expiry(self):
        ka = KeyAuthorization(
            key_id=KEY_ID,
            account=ACCOUNT,
            chain_id=42431,
            expiry=1893456000,
        )
        assert ka.expiry == 1893456000

    def test_create_with_limits(self):
        tl = TokenLimit(token=ALPHA_USD, limit=10**18)
        ka = KeyAuthorization(
            key_id=KEY_ID,
            account=ACCOUNT,
            chain_id=42431,
            limits=(tl,),
        )
        assert len(ka.limits) == 1

    def test_authorization_hash(self):
        ka = KeyAuthorization(
            key_id=KEY_ID,
            account=ACCOUNT,
            chain_id=42431,
        )
        h = ka.authorization_hash()
        assert isinstance(h, bytes)
        assert len(h) == 32

    def test_to_rlp(self):
        ka = KeyAuthorization(
            key_id=KEY_ID,
            account=ACCOUNT,
            chain_id=42431,
        )
        rlp = ka.to_rlp()
        assert isinstance(rlp, list)


# ---------------------------------------------------------------------------
# KeychainSignature & build helpers
# ---------------------------------------------------------------------------


class TestKeychain:
    def test_build_keychain_signature_length(self):
        sig = Signature(r=1, s=1, v=0)
        from tempo.types import as_address

        addr = as_address(ACCOUNT)
        raw = build_keychain_signature(sig, addr)
        assert len(raw) == 86

    def test_keychain_signature_parse(self):
        sig = Signature(r=1, s=1, v=0)
        from tempo.types import as_address

        addr = as_address(ACCOUNT)
        raw = build_keychain_signature(sig, addr)
        ks = KeychainSignature(raw=raw)
        assert len(ks.raw) == 86
        assert ks.inner_signature is not None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestKeychainConstants:
    def test_constants(self):
        from tempo.keychain import (
            INNER_SIGNATURE_LENGTH,
            KEYCHAIN_SIGNATURE_LENGTH,
            KEYCHAIN_SIGNATURE_TYPE,
        )

        assert KEYCHAIN_SIGNATURE_TYPE == 0x04
        assert KEYCHAIN_SIGNATURE_LENGTH == 86
        assert INNER_SIGNATURE_LENGTH == 65
