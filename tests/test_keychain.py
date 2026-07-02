"""Tests for tempo.keychain — access key models and key authorization."""

import rlp
from eth_account import Account
from eth_utils import keccak

from tempo import Call, Signer, TempoTransaction
from tempo.constants import ALPHA_USD, CHAIN_ID_MODERATO
from tempo.constants import PK_0 as ROOT_PK
from tempo.constants import PK_1 as ACCESS_PK
from tempo.contracts import TIP20
from tempo.keychain import (
    KEYCHAIN_SIGNATURE_TYPE,
    CallScope,
    KeyAuthorization,
    KeychainSignature,
    KeyRestrictions,
    SelectorRule,
    SignatureType,
    TokenLimit,
    build_keychain_signature,
    sign_tx_access_key,
)
from tempo.models import Signature
from tempo.transaction import get_sign_payload

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


def _make_tx() -> TempoTransaction:
    return TempoTransaction.create(
        chain_id=CHAIN_ID_MODERATO,
        gas_limit=100_000,
        max_fee_per_gas=2_000_000_000,
        max_priority_fee_per_gas=1_000_000_000,
        nonce=0,
        nonce_key=0,
        calls=(Call.create(to=ALPHA_USD, data=TIP20.fns.transfer(RECIPIENT, 1000).data),),
    )


class TestKeyAuthorizationRlp:
    def test_admin_to_rlp_is_canonical_trailing_form(self):
        ka = KeyAuthorization(key_id=KEY_ID, account=ACCOUNT, chain_id=1337, is_admin=True)
        # [chain_id, key_type, key_id, expiry, limits, allowed_calls, witness, is_admin, account]
        # trailing-canonical: absent middle optionals are explicit 0x80 (b""),
        # is_admin is the marker 1, account is present.
        assert ka.to_rlp() == [1337, 0, bytes(ka.key_id), b"", b"", b"", b"", 1, bytes(ka.account)]

    def test_signature_hash_is_keccak_of_rlp(self):
        ka = KeyAuthorization(key_id=KEY_ID, account=ACCOUNT, chain_id=1337, is_admin=True)
        assert ka.signature_hash() == keccak(rlp.encode(ka.to_rlp()))


class TestKeychainSignatureV2:
    def test_layout_type_byte_and_roundtrip(self):
        inner = Signature(r=1, s=1, v=27)
        ks = KeychainSignature.from_inner(inner, ACCOUNT)
        raw = ks.to_bytes()
        assert len(raw) == 86
        assert raw[0] == KEYCHAIN_SIGNATURE_TYPE  # 0x04
        assert bytes(ks.user_address) == bytes(build_keychain_signature(inner, ACCOUNT)[1:21])
        assert ks.inner_signature == inner

    def test_rejects_wrong_type_byte(self):
        import pytest

        bad = bytes([0x03]) + bytes(20) + bytes(65)
        with pytest.raises(ValueError, match="type byte"):
            KeychainSignature(raw=bad)


class TestSignTxAccessKey:
    def test_admin_access_key_matches_node_format(self):
        from eth_utils import keccak as _keccak

        root, access = Signer(ROOT_PK), Signer(ACCESS_PK)
        signed = sign_tx_access_key(_make_tx(), ACCESS_PK, root, is_admin=True)

        # key_authorization is a nested SignedKeyAuthorization: [auth, root_sig(65)]
        assert isinstance(signed.key_authorization, list) and len(signed.key_authorization) == 2
        auth_payload, root_sig_bytes = signed.key_authorization
        assert auth_payload == [
            CHAIN_ID_MODERATO,
            int(SignatureType.SECP256K1),
            bytes(access.address),
            b"",
            b"",
            b"",
            b"",
            1,
            bytes(root.address),
        ]
        assert len(root_sig_bytes) == 65

        # root grant signature uses canonical v in {27, 28} and recovers to root
        r = int.from_bytes(root_sig_bytes[:32], "big")
        s = int.from_bytes(root_sig_bytes[32:64], "big")
        v = root_sig_bytes[64]
        assert v in (27, 28)
        auth = KeyAuthorization(key_id=access.address, account=root.address, chain_id=CHAIN_ID_MODERATO, is_admin=True)
        recovered_root = Account._recover_hash(auth.signature_hash(), vrs=(v, r, s))
        assert bytes.fromhex(recovered_root[2:]) == bytes(root.address)

        # sender signature is a Keychain V2 blob whose inner recovers to the access
        # key over keccak256(0x04 || sig_hash || root)
        ks = signed.sender_signature
        assert isinstance(ks, KeychainSignature)
        assert bytes(ks.user_address) == bytes(root.address)
        sig_hash = get_sign_payload(signed)
        effective = _keccak(bytes([KEYCHAIN_SIGNATURE_TYPE]) + sig_hash + bytes(root.address))
        inner = ks.inner_signature
        recovered_key = Account._recover_hash(effective, vrs=(inner.v, inner.r, inner.s))
        assert bytes.fromhex(recovered_key[2:]) == bytes(access.address)
