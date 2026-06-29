"""Tests for tempo.constants."""

from eth_utils import to_checksum_address

from tempo.constants import (
    ACCOUNT_KEYCHAIN_ADDRESS,
    ALPHA_USD,
    BETA_USD,
    CHAIN_ID_DEVNET,
    CHAIN_ID_MAINNET,
    CHAIN_ID_MODERATO,
    DEFAULT_CHAIN_ID,
    DEFAULT_NONCE_KEY,
    FEE_MANAGER_ADDRESS,
    NONCE_ADDRESS,
    PATH_USD,
    RECEIVE_POLICY_GUARD_ADDRESS,
    RPC_URL_MAINNET,
    RPC_URL_MODERATO,
    SIGNATURE_VERIFIER_ADDRESS,
    STABLECOIN_DEX_ADDRESS,
    THETA_USD,
    TIP403_REGISTRY_ADDRESS,
    VALIDATOR_CONFIG_ADDRESS,
)


class TestChainIDs:
    def test_mainnet(self):
        assert CHAIN_ID_MAINNET == 4217

    def test_moderato(self):
        assert CHAIN_ID_MODERATO == 42431

    def test_devnet(self):
        assert CHAIN_ID_DEVNET == 31318

    def test_default(self):
        assert DEFAULT_CHAIN_ID == CHAIN_ID_MAINNET


class TestDefaults:
    def test_nonce_key(self):
        assert DEFAULT_NONCE_KEY == 0

    def test_rpc_urls(self):
        assert RPC_URL_MAINNET == "https://rpc.tempo.xyz"
        assert RPC_URL_MODERATO == "https://rpc.moderato.tempo.xyz"


class TestTokenAddresses:
    def _check(self, addr: str, expected_prefix: str):
        assert addr.startswith(expected_prefix)

    def test_path_usd(self):
        assert PATH_USD == to_checksum_address("0x20C0000000000000000000000000000000000000")

    def test_alpha_usd(self):
        assert ALPHA_USD == to_checksum_address("0x20C0000000000000000000000000000000000001")

    def test_beta_usd(self):
        assert BETA_USD == to_checksum_address("0x20C0000000000000000000000000000000000002")

    def test_theta_usd(self):
        assert THETA_USD == to_checksum_address("0x20C0000000000000000000000000000000000003")


class TestPrecompileAddresses:
    def test_fee_manager(self):
        assert FEE_MANAGER_ADDRESS == to_checksum_address("0xfeEC000000000000000000000000000000000000")

    def test_account_keychain(self):
        assert ACCOUNT_KEYCHAIN_ADDRESS == to_checksum_address("0xaAAAaaAA00000000000000000000000000000000")

    def test_dex(self):
        assert STABLECOIN_DEX_ADDRESS == to_checksum_address("0xDEc0000000000000000000000000000000000000")

    def test_nonce(self):
        assert NONCE_ADDRESS == to_checksum_address("0x4e4F4E4345000000000000000000000000000000")

    def test_tip403(self):
        assert TIP403_REGISTRY_ADDRESS == to_checksum_address("0x403c000000000000000000000000000000000000")

    def test_signature_verifier(self):
        assert SIGNATURE_VERIFIER_ADDRESS == to_checksum_address("0x5165300000000000000000000000000000000000")

    def test_receive_policy_guard(self):
        assert RECEIVE_POLICY_GUARD_ADDRESS == to_checksum_address("0xB10C000000000000000000000000000000000000")

    def test_validator_config(self):
        assert VALIDATOR_CONFIG_ADDRESS == to_checksum_address("0xCccCcCCC00000000000000000000000000000000")
