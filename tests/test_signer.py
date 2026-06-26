"""Tests for tempo.signer."""

import pytest
from tempo.signer import Signer, recover_address, verify_signature
from tempo.models import Signature
from eth_utils import keccak

# Anvil/Hardhat test private key #0
TEST_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

# Anvil/Hardhat test private key #1
TEST_PK_2 = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
TEST_ADDR_2 = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


class TestSigner:
    def test_address(self):
        signer = Signer(TEST_PK)
        addr = "0x" + signer.address.hex()
        assert addr.lower() == TEST_ADDR.lower()

    def test_checksum_address(self):
        signer = Signer(TEST_PK)
        assert signer.checksum_address == TEST_ADDR

    def test_sign_and_recover(self):
        signer = Signer(TEST_PK)
        msg_hash = keccak(b"hello")
        sig = signer.sign(msg_hash)

        recovered = recover_address(msg_hash, sig)
        recovered_hex = "0x" + recovered.hex()
        assert recovered_hex.lower() == TEST_ADDR.lower()

    def test_verify_signature(self):
        signer = Signer(TEST_PK)
        msg_hash = keccak(b"test message")
        sig = signer.sign(msg_hash)
        assert signer.verify_signature(msg_hash, sig)

    def test_verify_signature_wrong_signer(self):
        signer = Signer(TEST_PK)
        signer2 = Signer(TEST_PK_2)
        msg_hash = keccak(b"test")
        sig = signer.sign(msg_hash)
        assert not signer2.verify_signature(msg_hash, sig)

    def test_sign_data(self):
        signer = Signer(TEST_PK)
        sig = signer.sign_data(b"arbitrary data")
        msg_hash = keccak(b"arbitrary data")
        recovered = recover_address(msg_hash, sig)
        recovered_hex = "0x" + recovered.hex()
        assert recovered_hex.lower() == TEST_ADDR.lower()

    def test_private_key_without_0x_prefix(self):
        signer = Signer(TEST_PK[2:])  # strip 0x
        assert signer.checksum_address == TEST_ADDR


class TestRecoverAddress:
    def test_recover_valid(self):
        signer = Signer(TEST_PK)
        msg_hash = keccak(b"hello")
        sig = signer.sign(msg_hash)
        addr = recover_address(msg_hash, sig)
        assert "0x" + addr.hex() == TEST_ADDR.lower()  # checksum differs on case
        assert addr.hex() == bytes.fromhex(TEST_ADDR[2:]).hex()

    def test_recover_fee_payer(self):
        signer = Signer(TEST_PK_2)
        msg_hash = keccak(b"fee payer message")
        sig = signer.sign(msg_hash)
        addr = recover_address(msg_hash, sig)
        assert addr.hex() == bytes.fromhex(TEST_ADDR_2[2:]).hex()

    def test_verify_signature_function(self):
        signer = Signer(TEST_PK)
        msg_hash = keccak(b"verify me")
        sig = signer.sign(msg_hash)
        assert verify_signature(msg_hash, sig, TEST_ADDR)
