"""Tests for tempo.keychain — access key models and key authorization."""

import pytest
import rlp
from eth_utils import keccak

from tempo import Call, Signer, TempoTransaction
from tempo.constants import ALPHA_USD, CHAIN_ID_MODERATO
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
from tempo.signer import recover_address
from tempo.transaction import get_sign_payload, verify_signature
from tempo.types import as_address
from tests.constants import PK_0 as ROOT_PK
from tests.constants import PK_1 as ACCESS_PK

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

    def test_to_rlp_omits_zero_period(self):
        # Node wire is trailing-canonical Option<NonZeroU64>: period 0 must be absent.
        tl = TokenLimit(token=ALPHA_USD, limit=10**18)
        assert tl.to_rlp() == [bytes.fromhex(ALPHA_USD[2:]), 10**18]

    def test_to_rlp_keeps_nonzero_period(self):
        tl = TokenLimit(token=ALPHA_USD, limit=10**18, period=86400)
        assert tl.to_rlp() == [bytes.fromhex(ALPHA_USD[2:]), 10**18, 86400]


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

    def test_to_rlp_folds_selector_into_rule(self):
        # The wire has no top-level selector; empty rules would mean "any
        # function", so transfer() must encode a single-selector rule.
        cs = CallScope.transfer(ALPHA_USD)
        assert cs.to_rlp() == [bytes(cs.target), [[bytes.fromhex("a9059cbb"), []]]]

    def test_to_rlp_unrestricted_has_no_rules(self):
        cs = CallScope.unrestricted(target=RECIPIENT)
        assert cs.to_rlp() == [bytes(cs.target), []]

    def test_to_rlp_explicit_rules_win(self):
        rule = SelectorRule.create(selector=bytes.fromhex("a9059cbb"), recipients=(RECIPIENT,))
        cs = CallScope.with_selector(target=ALPHA_USD, selector=bytes.fromhex("a9059cbb"), selector_rules=(rule,))
        assert cs.to_rlp() == [bytes(cs.target), [[bytes.fromhex("a9059cbb"), [bytes(as_address(RECIPIENT))]]]]


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

    def test_none_vs_empty_limits_and_calls(self):
        # None = absent (unlimited/unrestricted); () = present empty (deny-all).
        absent = KeyAuthorization(key_id=KEY_ID, account=ACCOUNT, chain_id=1)
        assert absent.to_rlp()[4] == b"" and absent.to_rlp()[5] == b""
        deny = KeyAuthorization(key_id=KEY_ID, account=ACCOUNT, chain_id=1, limits=(), allowed_calls=())
        assert deny.to_rlp()[4] == [] and deny.to_rlp()[5] == []
        assert absent.signature_hash() != deny.signature_hash()

    def test_account_omitted_trims_trailing_fields(self):
        ka = KeyAuthorization(key_id=KEY_ID, chain_id=1)
        # No optional set: everything after key_id is trimmed.
        assert ka.to_rlp() == [1, 0, bytes(ka.key_id)]

    def test_admin_rejects_restrictions(self):
        with pytest.raises(ValueError, match="admin"):
            KeyAuthorization(key_id=KEY_ID, account=ACCOUNT, chain_id=1, is_admin=True, expiry=123)
        with pytest.raises(ValueError, match="admin"):
            KeyAuthorization(key_id=KEY_ID, account=ACCOUNT, chain_id=1, is_admin=True, limits=())
        with pytest.raises(ValueError, match="admin"):
            KeyAuthorization(key_id=KEY_ID, account=ACCOUNT, chain_id=1, is_admin=True, allowed_calls=())

    def test_witness_must_be_32_bytes(self):
        with pytest.raises(ValueError, match="witness"):
            KeyAuthorization(key_id=KEY_ID, account=ACCOUNT, chain_id=1, witness=b"\x11" * 65)
        ka = KeyAuthorization(key_id=KEY_ID, account=ACCOUNT, chain_id=1, witness=b"\x11" * 32)
        assert ka.to_rlp()[6] == b"\x11" * 32


# ---------------------------------------------------------------------------
# KeychainSignature & build helpers
# ---------------------------------------------------------------------------


class TestKeychain:
    def test_build_keychain_signature_length(self):
        raw = build_keychain_signature(Signature(r=1, s=1, v=0), as_address(ACCOUNT))
        assert len(raw) == 86

    def test_keychain_signature_parse(self):
        raw = build_keychain_signature(Signature(r=1, s=1, v=0), as_address(ACCOUNT))
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

    def test_admin_rlp_golden_vector(self):
        # Byte-exact wire form accepted by the node (verified against a live
        # tempo dev node); pins the encoding independent of to_rlp/signature_hash.
        ka = KeyAuthorization(key_id=KEY_ID, account=ACCOUNT, chain_id=1337, is_admin=True)
        expected = (
            "f38205398094aaaaaaaa00000000000000000000000000000000808080800194f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
        )
        assert rlp.encode(ka.to_rlp()).hex() == expected
        assert ka.signature_hash() == keccak(bytes.fromhex(expected))


class TestKeychainSignatureV2:
    def test_layout_type_byte_and_roundtrip(self):
        inner = Signature(r=1, s=1, v=27)
        ks = KeychainSignature.from_inner(inner, ACCOUNT)
        raw = ks.to_bytes()
        assert len(raw) == 86
        assert raw[0] == KEYCHAIN_SIGNATURE_TYPE  # 0x04
        assert raw[1:21] == bytes(as_address(ACCOUNT))
        assert raw[21:] == inner.to_canonical_bytes()
        assert ks.user_address == as_address(ACCOUNT)
        assert ks.inner_signature == inner

    def test_from_inner_canonicalizes_v(self):
        # The node re-encodes the inner sig with v in {27, 28} when computing
        # the canonical tx bytes/hash, so the envelope must embed that form.
        ks = KeychainSignature.from_inner(Signature(r=1, s=1, v=0), ACCOUNT)
        assert ks.to_bytes()[-1] == 27
        assert build_keychain_signature(Signature(r=1, s=1, v=1), ACCOUNT)[-1] == 28

    def test_rejects_wrong_type_byte(self):
        bad = bytes([0x03]) + bytes(20) + bytes(65)
        with pytest.raises(ValueError, match="type byte"):
            KeychainSignature(raw=bad)


class TestSignTxAccessKey:
    def test_admin_access_key_matches_node_format(self):
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

        # root grant signature uses canonical v in {27, 28} and recovers to root
        root_sig = Signature.from_bytes(root_sig_bytes)
        assert root_sig.v in (27, 28)
        auth = KeyAuthorization(key_id=access.address, account=root.address, chain_id=CHAIN_ID_MODERATO, is_admin=True)
        assert recover_address(auth.signature_hash(), root_sig) == root.address

        # sender signature is a Keychain V2 blob whose inner recovers to the
        # access key over the domain-separated keychain hash
        ks = signed.sender_signature
        assert isinstance(ks, KeychainSignature)
        assert ks.user_address == as_address(root.address)
        effective = KeychainSignature.signing_hash(get_sign_payload(signed), root.address)
        assert recover_address(effective, ks.inner_signature) == access.address

        # verify_signature is keychain-aware and returns the root (tx sender)
        assert verify_signature(signed) == root.address

    def test_restricted_access_key(self):
        root = Signer(ROOT_PK)
        signed = sign_tx_access_key(
            _make_tx(),
            ACCESS_PK,
            root,
            is_admin=False,
            limits=(TokenLimit(token=ALPHA_USD, limit=10_000),),
            allowed_calls=(CallScope.transfer(ALPHA_USD),),
        )
        auth_payload, _ = signed.key_authorization
        token = bytes.fromhex(ALPHA_USD[2:])
        assert auth_payload[4] == [[token, 10_000]]  # period omitted
        assert auth_payload[5] == [[token, [[bytes.fromhex("a9059cbb"), []]]]]

    def test_rejects_non_secp256k1_key_type(self):
        with pytest.raises(ValueError, match="SECP256K1"):
            sign_tx_access_key(_make_tx(), ACCESS_PK, Signer(ROOT_PK), key_type=SignatureType.P256)

    def test_rejects_admin_with_restrictions(self):
        with pytest.raises(ValueError, match="admin"):
            sign_tx_access_key(_make_tx(), ACCESS_PK, Signer(ROOT_PK), expiry=1893456000)
