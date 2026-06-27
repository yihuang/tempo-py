"""
tempo-py — Tempo blockchain Python SDK.

Built on web3.py with pure functions and data model classes.

Core modules:

- ``tempo.models`` — ``Call``, ``Signature``, ``TempoTransaction`` data models
- ``tempo.types`` — ``Address``, ``Hash32``, type coercion helpers
- ``tempo.constants`` — chain IDs, RPC URLs, token/precompile addresses
- ``tempo.signer`` — ``Signer``, ``recover_address``, ``verify_signature``
- ``tempo.transaction`` — RLP serialization, signing helpers, ``Builder``, TIP-20 encoding
- ``tempo.client`` — JSON-RPC client for Tempo
- ``tempo.keychain`` — access key models (``KeyRestrictions``, ``KeyAuthorization``, ``CallScope``)
- ``tempo.contracts`` — typed call builders for TIP-20, AccountKeychain, etc.

Quick start::

    from tempo import TempoTransaction, Call, Signer
    from tempo.constants import CHAIN_ID_MODERATO

    tx = TempoTransaction.create(
        chain_id=CHAIN_ID_MODERATO,
        gas_limit=100_000,
        calls=(Call.create(to="0x..."),),
    )
    signer = Signer("0x...")
    signed = sign_transaction(tx, signer)
    ...

The Go SDK is at https://github.com/tempoxyz/tempo-go
"""

from .client import Client, JSONRPCError
from .constants import (
    CHAIN_ID_DEVNET,
    CHAIN_ID_MAINNET,
    CHAIN_ID_MODERATO,
    CHAIN_ID_TESTNET,
    DEFAULT_CHAIN_ID,
    RPC_URL_DEVNET,
    RPC_URL_MAINNET,
    RPC_URL_MODERATO,
)
from .models import (
    AccessListItem,
    Call,
    Signature,
    TempoTransaction,
)
from .signer import Signer, recover_address, verify_signature
from .transaction import (
    Builder,
    add_fee_payer_signature,
    get_fee_payer_sign_payload,
    get_sign_payload,
    serialize,
    serialize_for_fee_payer_signing,
    serialize_for_signing,
    sign_transaction,
    verify_fee_payer_signature,
    verify_signature as verify_tx_signature,
)
from .types import (
    Address,
    BytesLike,
    Hash32,
    Selector,
    as_address,
    as_bytes,
    as_hash32,
    as_optional_address,
    as_selector,
    validate_nonempty_address,
)

__version__ = "0.1.0"

__all__ = [
    # Types
    "Address",
    "Hash32",
    "Selector",
    "BytesLike",
    "as_address",
    "as_bytes",
    "as_hash32",
    "as_optional_address",
    "as_selector",
    "validate_nonempty_address",
    # Models
    "Call",
    "AccessListItem",
    "Signature",
    "TempoTransaction",
    # Signer
    "Signer",
    "recover_address",
    "verify_signature",
    # Transaction
    "Builder",
    "serialize",
    "serialize_for_signing",
    "serialize_for_fee_payer_signing",
    "sign_transaction",
    "add_fee_payer_signature",
    "get_sign_payload",
    "get_fee_payer_sign_payload",
    "verify_tx_signature",
    "verify_fee_payer_signature",
    # Client
    "Client",
    "JSONRPCError",
    # Constants
    "CHAIN_ID_MAINNET",
    "CHAIN_ID_MODERATO",
    "CHAIN_ID_DEVNET",
    "CHAIN_ID_TESTNET",
    "DEFAULT_CHAIN_ID",
    "RPC_URL_MAINNET",
    "RPC_URL_MODERATO",
    "RPC_URL_DEVNET",
]
