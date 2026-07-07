"""Tests for tempo.transaction."""

import time

import pytest
import rlp

from tempo import Call, Signer, TempoTransaction
from tempo.constants import ACCOUNT_KEYCHAIN_ADDRESS, ALPHA_USD, CHAIN_ID_MODERATO
from tempo.contracts import TIP20
from tempo.transaction import (
    Builder as TxBuilder,
)
from tempo.transaction import (
    add_fee_payer_signature,
    get_fee_payer_sign_payload,
    get_sign_payload,
    serialize,
    serialize_for_fee_payer_signing,
    serialize_for_signing,
    sign_transaction,
    verify_fee_payer_signature,
)
from tempo.transaction import (
    verify_signature as verify_tx_sig,
)
from tests.constants import PK_0 as TEST_PK
from tests.constants import PK_1 as FEE_PAYER_PK

RECIPIENT = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


# ---------------------------------------------------------------------------
# TIP-20 encoding via TIP20_CONTRACT
# ---------------------------------------------------------------------------


class TestTIP20Encoding:
    def test_transfer_selector_and_size(self) -> None:
        data = bytes(TIP20.fns.transfer(RECIPIENT, 10**18).data)
        assert data[:4] == bytes(TIP20.fns.transfer.selector)
        assert len(data) == 68

    def test_transfer_contains_recipient(self) -> None:
        data = bytes(TIP20.fns.transfer(RECIPIENT, 10**18).data)
        recipient_bytes = bytes.fromhex(RECIPIENT[2:])
        assert data[16:36] == recipient_bytes
        assert int.from_bytes(data[36:68], "big") == 10**18

    def test_approve(self) -> None:
        data = bytes(TIP20.fns.approve(RECIPIENT, 10**18).data)
        assert data[:4] == bytes(TIP20.fns.approve.selector)

    def test_transfer_with_memo(self) -> None:
        memo = b"\x01" * 32
        data = bytes(TIP20.fns.transferWithMemo(RECIPIENT, 10**18, memo).data)
        assert data[:4] == bytes(TIP20.fns.transferWithMemo.selector)
        assert len(data) == 100
        assert data[68:100] == memo


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_serialize_for_signing_prefix(self) -> None:
        tx = _make_tx()
        ser = serialize_for_signing(tx)
        assert ser.startswith("0x76")

    def test_serialize_signed_prefix(self) -> None:
        tx = _make_tx()
        signed = sign_transaction(tx, Signer(TEST_PK))
        ser = serialize(signed)
        assert ser.startswith("0x76")

    def test_serialize_for_fee_payer_signing(self) -> None:
        tx = _make_tx(awaiting_fee_payer=True)
        signer = Signer(TEST_PK)
        signed_by_sender = sign_transaction(tx, signer)
        ser = serialize_for_fee_payer_signing(signed_by_sender, signer.address)
        assert ser.startswith("0x78")

    def test_fee_payer_payload_includes_fee_token(self) -> None:
        tx = _make_tx(awaiting_fee_payer=True, fee_token=ALPHA_USD)
        signer = Signer(TEST_PK)
        signed = sign_transaction(tx, signer)

        sender_fields = rlp.decode(bytes.fromhex(serialize_for_signing(signed).removeprefix("0x76")))
        fee_payer_fields = rlp.decode(
            bytes.fromhex(serialize_for_fee_payer_signing(signed, signer.address).removeprefix("0x78"))
        )

        assert sender_fields[10] == b""
        assert fee_payer_fields[10] == bytes.fromhex(ALPHA_USD.removeprefix("0x"))

    def test_sender_payload_skips_fee_token_once_fee_payer_signed(self) -> None:
        # An attached fee payer sig skips fee_token even with awaiting_fee_payer
        # unset, matching upstream's fee_payer_signature.is_some() rule.
        tx = _make_tx(awaiting_fee_payer=False, fee_token=ALPHA_USD)
        signed = sign_transaction(tx, Signer(TEST_PK))
        final = add_fee_payer_signature(signed, Signer(FEE_PAYER_PK))
        assert not final.awaiting_fee_payer
        assert final.fee_payer_signature is not None

        sender_fields = rlp.decode(bytes.fromhex(serialize_for_signing(final).removeprefix("0x76")))
        assert sender_fields[10] == b""

    def test_signed_tx_hex_length(self) -> None:
        tx = _make_tx()
        signed = sign_transaction(tx, Signer(TEST_PK))
        ser = serialize(signed)
        assert 200 < len(ser) < 2000

    def test_empty_calls_list(self) -> None:
        tx = TempoTransaction.create(chain_id=CHAIN_ID_MODERATO, gas_limit=100_000, calls=())
        ser = serialize_for_signing(tx)
        assert ser.startswith("0x76")


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


class TestSigning:
    def test_sign_transaction(self) -> None:
        tx = _make_tx()
        signed = sign_transaction(tx, Signer(TEST_PK))
        assert signed.has_sender_signature
        assert signed.sender_signature is not None

    def test_sign_adds_sender_address(self) -> None:
        tx = _make_tx()
        signer = Signer(TEST_PK)
        signed = sign_transaction(tx, signer)
        assert signed.sender_address == signer.address

    def test_verify_signature(self) -> None:
        tx = _make_tx()
        signer = Signer(TEST_PK)
        signed = sign_transaction(tx, signer)
        addr = verify_tx_sig(signed)
        assert addr == signer.address

    def test_verify_fails_on_unsigned(self) -> None:
        tx = _make_tx()
        with pytest.raises(ValueError, match="no sender signature"):
            verify_tx_sig(tx)

    def test_fee_payer_flow(self) -> None:
        user = Signer(TEST_PK)
        fee_payer = Signer(FEE_PAYER_PK)
        tx = _make_tx(awaiting_fee_payer=True)
        signed_user = sign_transaction(tx, user)
        assert signed_user.awaiting_fee_payer
        final = add_fee_payer_signature(signed_user, fee_payer)
        assert final.has_fee_payer_signature
        fee_payer_addr = verify_fee_payer_signature(final, user.address)
        assert fee_payer_addr == fee_payer.address

    def test_fee_payer_needs_sender_sig_first(self) -> None:
        tx = _make_tx()
        with pytest.raises(ValueError, match="must have sender signature"):
            add_fee_payer_signature(tx, Signer(FEE_PAYER_PK))

    def test_sign_payload_changes_with_calls(self) -> None:
        tx1 = _make_tx()
        tx2 = _make_tx()
        tx2 = TempoTransaction.create(
            chain_id=tx2.chain_id,
            gas_limit=tx2.gas_limit,
            max_fee_per_gas=tx2.max_fee_per_gas,
            calls=(Call.create(to=RECIPIENT, value=999),),
        )
        h1 = get_sign_payload(tx1)
        h2 = get_sign_payload(tx2)
        assert h1 != h2


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class TestBuilder:
    def test_build_basic(self) -> None:
        tx = (
            TxBuilder()
            .chain_id(CHAIN_ID_MODERATO)
            .gas_limit(100_000)
            .max_fee_per_gas(2_000_000_000)
            .nonce(0)
            .add_call(to=ALPHA_USD, data=TIP20.fns.transfer(RECIPIENT, 10**18).data)
            .build()
        )
        assert tx.chain_id == CHAIN_ID_MODERATO
        assert len(tx.calls) == 1

    def test_build_multiple_calls(self) -> None:
        tx = (
            TxBuilder()
            .chain_id(CHAIN_ID_MODERATO)
            .gas_limit(200_000)
            .add_call(to=ALPHA_USD, data=b"")
            .add_call(to=ACCOUNT_KEYCHAIN_ADDRESS, data=b"")
            .build()
        )
        assert len(tx.calls) == 2

    def test_build_signing_roundtrip(self) -> None:
        tx = (
            TxBuilder()
            .chain_id(CHAIN_ID_MODERATO)
            .gas_limit(100_000)
            .max_fee_per_gas(2_000_000_000)
            .nonce(0)
            .add_call(to=ALPHA_USD, data=TIP20.fns.transfer(RECIPIENT, 10**18).data)
            .build()
        )
        signed = sign_transaction(tx, Signer(TEST_PK))
        ser = serialize(signed)
        assert ser.startswith("0x76")

    def test_build_with_validity_window(self) -> None:
        now = int(time.time())
        tx = (
            TxBuilder()
            .chain_id(CHAIN_ID_MODERATO)
            .valid_after(now)
            .valid_before(now + 3600)
            .add_call(to=ALPHA_USD, data=b"")
            .build()
        )
        assert tx.valid_after == now
        assert tx.valid_before == now + 3600

    def test_build_with_fee_token(self) -> None:
        tx = TxBuilder().chain_id(CHAIN_ID_MODERATO).fee_token(ALPHA_USD).add_call(to=RECIPIENT, data=b"").build()
        assert tx.fee_token is not None


# ---------------------------------------------------------------------------
# Sign payload
# ---------------------------------------------------------------------------


class TestPayload:
    def test_get_sign_payload_type(self) -> None:
        tx = _make_tx()
        h = get_sign_payload(tx)
        assert isinstance(h, bytes)
        assert len(h) == 32

    def test_get_fee_payer_sign_payload_type(self) -> None:
        tx = _make_tx(awaiting_fee_payer=True)
        signer = Signer(TEST_PK)
        signed = sign_transaction(tx, signer)
        h = get_fee_payer_sign_payload(signed, signer.address)
        assert isinstance(h, bytes)
        assert len(h) == 32


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tx(awaiting_fee_payer: bool = False, fee_token: object = None) -> TempoTransaction:
    return TempoTransaction.create(
        chain_id=CHAIN_ID_MODERATO,
        gas_limit=100_000,
        max_fee_per_gas=2_000_000_000,
        max_priority_fee_per_gas=1_000_000_000,
        nonce=0,
        nonce_key=0,
        awaiting_fee_payer=awaiting_fee_payer,
        fee_token=fee_token,
        calls=(Call.create(to=ALPHA_USD, data=TIP20.fns.transfer(RECIPIENT, 10**18).data),),
    )
