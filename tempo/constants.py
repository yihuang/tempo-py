"""Canonical chain IDs, RPC URLs, precompile addresses, and token addresses."""

from eth_utils import to_checksum_address

# ---------------------------------------------------------------------------
# Chain IDs
# ---------------------------------------------------------------------------
CHAIN_ID_MAINNET = 4217
CHAIN_ID_MODERATO = 42431
CHAIN_ID_DEVNET = 31318
CHAIN_ID_TESTNET = 42429  # alias for Moderato

# ---------------------------------------------------------------------------
# RPC URLs
# ---------------------------------------------------------------------------
RPC_URL_MAINNET = "https://rpc.tempo.xyz"
RPC_URL_MODERATO = "https://rpc.moderato.tempo.xyz"
RPC_URL_DEVNET = "https://rpc.devnet.tempo.xyz"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_NONCE_KEY = 0
DEFAULT_CHAIN_ID = CHAIN_ID_MAINNET

# ---------------------------------------------------------------------------
# TIP-20 Token addresses (from StdTokens.sol)
# ---------------------------------------------------------------------------
PATH_USD = to_checksum_address("0x20C0000000000000000000000000000000000000")
ALPHA_USD = to_checksum_address("0x20C0000000000000000000000000000000000001")
BETA_USD = to_checksum_address("0x20C0000000000000000000000000000000000002")
THETA_USD = to_checksum_address("0x20C0000000000000000000000000000000000003")

# ---------------------------------------------------------------------------
# Precompile addresses (from StdPrecompiles.sol)
# ---------------------------------------------------------------------------

# Genesis
FEE_MANAGER_ADDRESS = to_checksum_address("0xfeEC000000000000000000000000000000000000")
TIP403_REGISTRY_ADDRESS = to_checksum_address("0x403c000000000000000000000000000000000000")
TIP20_FACTORY_ADDRESS = to_checksum_address("0x20Fc000000000000000000000000000000000000")
STABLECOIN_DEX_ADDRESS = to_checksum_address("0xDEc0000000000000000000000000000000000000")
NONCE_ADDRESS = to_checksum_address("0x4e4F4E4345000000000000000000000000000000")
VALIDATOR_CONFIG_ADDRESS = to_checksum_address("0xCccCcCCC00000000000000000000000000000000")
ACCOUNT_KEYCHAIN_ADDRESS = to_checksum_address("0xaAAAaaAA00000000000000000000000000000000")
VALIDATOR_CONFIG_V2_ADDRESS = to_checksum_address("0xCccCcCCC00000000000000000000000000000001")

# T3+ (TIP-1020 SignatureVerifier, virtual addresses)
ADDRESS_REGISTRY_ADDRESS = to_checksum_address("0xFDC0000000000000000000000000000000000000")
SIGNATURE_VERIFIER_ADDRESS = to_checksum_address("0x5165300000000000000000000000000000000000")

# T5+ (TIP-1034 channel reserve; ASCII "MPP" — mod.rs only, not StdPrecompiles.sol yet)
TIP20_CHANNEL_RESERVE_ADDRESS = to_checksum_address("0x4D50500000000000000000000000000000000000")

# T6+ (TIP-1028 inbound transfer guard)
RECEIVE_POLICY_GUARD_ADDRESS = to_checksum_address("0xB10C000000000000000000000000000000000000")

# T7+ (TIP-1060 storage credits)
STORAGE_CREDITS_ADDRESS = to_checksum_address("0x1060000000000000000000000000000000000000")
