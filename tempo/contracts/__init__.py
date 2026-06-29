"""Tempo contract addresses and typed call builders for precompiles and tokens.

Provides typed helper classes that return :class:`~tempo.models.Call` objects
ready to use in a :class:`~tempo.models.TempoTransaction`.
"""

from .addresses import (
    ACCOUNT_KEYCHAIN_ADDRESS,
    ALPHA_USD,
    BETA_USD,
    FEE_MANAGER_ADDRESS,
    NONCE_ADDRESS,
    PATH_USD,
    RECEIVE_POLICY_GUARD_ADDRESS,
    SIGNATURE_VERIFIER_ADDRESS,
    STABLECOIN_DEX_ADDRESS,
    THETA_USD,
    TIP20_FACTORY_ADDRESS,
    TIP20_REWARDS_REGISTRY_ADDRESS,
    TIP403_REGISTRY_ADDRESS,
    VALIDATOR_CONFIG_ADDRESS,
)
from .keychain import ACCOUNT_KEYCHAIN, AccountKeychain
from .tip20 import (
    TIP20,
    TIP20_ROLES,
)

__all__ = [
    # Global Contract instances (eth-contract style)
    "TIP20",
    "TIP20_ROLES",
    "ACCOUNT_KEYCHAIN",
    # Token addresses
    "ALPHA_USD",
    "BETA_USD",
    "THETA_USD",
    "PATH_USD",
    # Precompile addresses
    "ACCOUNT_KEYCHAIN_ADDRESS",
    "FEE_MANAGER_ADDRESS",
    "STABLECOIN_DEX_ADDRESS",
    "NONCE_ADDRESS",
    "TIP20_FACTORY_ADDRESS",
    "TIP20_REWARDS_REGISTRY_ADDRESS",
    "TIP403_REGISTRY_ADDRESS",
    "RECEIVE_POLICY_GUARD_ADDRESS",
    "SIGNATURE_VERIFIER_ADDRESS",
    "VALIDATOR_CONFIG_ADDRESS",
]
