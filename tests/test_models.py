"""Tests for tempo.models."""

import pytest
from tempo.models import Call, Signature, AccessListItem, TempoTransaction
from tempo.constants import CHAIN_ID_MODERATO


class TestCall:
    def test_create_basic(self):
        c = Call.create(to="0x" + "ab" * 20)
        assert len(c.to) == 20
        assert c.value == 0
        assert c.data == b""

    def test_create_with_value(self):
        c = Call.create(to="0x" + "ab" * 20, value=100, data="0xdeadbeef")
        assert c.value == 100
        assert c.data == b"\xde\xad\xbe\xef"

    def test_rejects_negative_value(self):
        with pytest.raises(ValueError):
            Call.create(to="0x" + "ab" * 20, value=-1)

    def test_empty_to_for_contract_creation(self):
        c = Call.create(to=b"", value=0)
        assert c.to == b""

    def test_as_rlp_list(self):
        c = Call.create(to="0x" + "01" * 20, value=42, data="0x1234")
        rlp = c.as_rlp_list()
        assert len(rlp) == 3
        assert rlp[0] == b"\x01" * 20
        assert rlp[1] == 42
        assert rlp[2] == b"\x12\x34"


class TestSignature:
    def test_create_valid(self):
        sig = Signature(r=1, s=1, v=0)
        assert sig.r == 1
        assert sig.y_parity == 0

    def test_v_normalization(self):
        assert Signature(r=1, s=1, v=27).y_parity == 0
        assert Signature(r=1, s=1, v=28).y_parity == 1
        assert Signature(r=1, s=1, v=0).y_parity == 0
        assert Signature(r=1, s=1, v=1).y_parity == 1

    def test_to_bytes_roundtrip(self):
        sig = Signature(r=12345, s=67890, v=27)
        raw = sig.to_bytes()
        assert len(raw) == 65
        restored = Signature.from_bytes(raw)
        assert restored.r == 12345
        assert restored.s == 67890
        assert restored.v == 27

    def test_to_rlp_list(self):
        sig = Signature(r=99, s=100, v=28)
        rlp = sig.to_rlp_list()
        assert rlp == [1, 99, 100]

    def test_rejects_invalid_r(self):
        with pytest.raises(ValueError):
            Signature(r=0, s=1, v=0)

    def test_rejects_invalid_s(self):
        n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
        with pytest.raises(ValueError):
            Signature(r=1, s=n, v=0)

    def test_rejects_invalid_v(self):
        with pytest.raises(ValueError):
            Signature(r=1, s=1, v=99)


class TestTempoTransaction:
    def test_create_empty(self):
        tx = TempoTransaction.create()
        assert tx.chain_id == 4217  # mainnet default
        assert tx.gas_limit == 21_000
        assert tx.calls == ()

    def test_create_with_calls(self):
        c = Call.create(to="0x" + "ab" * 20, value=1)
        tx = TempoTransaction.create(chain_id=CHAIN_ID_MODERATO, calls=(c,))
        assert len(tx.calls) == 1
        assert tx.chain_id == 42431

    def test_signature_flags(self):
        tx = TempoTransaction.create()
        assert not tx.has_sender_signature
        assert not tx.has_fee_payer_signature

    def test_clone_drops_signatures(self):
        import attrs
        tx = TempoTransaction.create()
        signed = attrs.evolve(tx, sender_signature=Signature(r=1, s=1, v=0))
        assert signed.has_sender_signature
        cloned = signed.clone()
        assert not cloned.has_sender_signature
        assert cloned.chain_id == tx.chain_id

    def test_from_dict_camelCase(self):
        tx = TempoTransaction.from_dict({
            "chainId": 42431,
            "gas": 100_000,
            "maxFeePerGas": 2_000_000_000,
            "nonce": 5,
            "to": "0x" + "01" * 20,
            "value": 1000,
        })
        assert tx.chain_id == 42431
        assert tx.gas_limit == 100_000
        assert tx.max_fee_per_gas == 2_000_000_000
        assert tx.nonce == 5
        assert len(tx.calls) == 1
        assert tx.calls[0].value == 1000

    def test_from_dict_snake_case(self):
        tx = TempoTransaction.from_dict({
            "chain_id": 42431,
            "gas_limit": 100_000,
            "calls": [{"to": "0x" + "01" * 20, "value": 42}],
        })
        assert tx.chain_id == 42431
        assert tx.calls[0].value == 42
