"""Typed call builders for the AccountKeychain precompile, built on eth-contract.

Uses ``Contract.from_abi()`` with human-readable ABI + struct definitions for
Web3-agnostic calldata building.

For the common case (authorize_key with empty restrictions, revoke_key,
update_spending_limit) the typed methods cover everything. Complex nested
scopes (set_allowed_calls with many rules) use the same Contract directly.
"""

from eth_contract import Contract
from web3 import Web3

from ..constants import ACCOUNT_KEYCHAIN_ADDRESS
from ..models import Call

# ---------------------------------------------------------------------------
# Global Contract instance — shared, reusable, public
# ---------------------------------------------------------------------------
ACCOUNT_KEYCHAIN = Contract.from_abi(
    [
        """struct TokenLimit {
        address token;
        uint256 amount;
        uint64 period;
    }""",
        """struct SelectorRule {
        bytes4 selector;
        address[] recipients;
    }""",
        """struct CallScope {
        address target;
        SelectorRule[] selectorRules;
    }""",
        """struct KeyRestrictions {
        uint64 expiry;
        bool enforceLimits;
        TokenLimit[] limits;
        bool allowAnyCalls;
        CallScope[] allowedCalls;
    }""",
        "function authorizeKey(address keyId, uint8 signatureType, KeyRestrictions restrictions)",
        "function revokeKey(address keyId)",
        "function updateSpendingLimit(address keyId, address token, uint256 newLimit)",
        "function setAllowedCalls(address keyId, CallScope[] scopes)",
        "function removeAllowedCalls(address keyId, address target)",
        "function authorizeAdminKey(address keyId, uint8 signatureType, bytes32 witness)",
        "function burnKeyAuthorizationWitness(bytes32 witness)",
        "function isKeyAuthorizationWitnessBurned(address account, bytes32 witness) view returns (bool)",
        "function isAdminKey(address account, address keyId) view returns (bool)",
    ]
)


def _build_restrictions_tuple(
    expiry: int = 0,
    enforce_limits: bool = False,
    limits: list | None = None,
    allow_any_calls: bool = True,
    allowed_calls: list | None = None,
) -> tuple:
    """Build a KeyRestrictions tuple for eth-contract ABI encoding.

    Each ``TokenLimit`` is ``(token_address, amount, period)`` as a tuple.
    Each ``CallScope`` is ``(target, [(selector, [recipients...]), ...])``.
    """
    return (
        expiry,
        enforce_limits,
        [tuple(lim) for lim in (limits or [])],
        allow_any_calls,
        [(_scope_target(s), _scope_rules(s)) for s in (allowed_calls or [])],
    )


def _scope_target(scope: object) -> str:
    if isinstance(scope, tuple):
        return scope[0]
    return scope.target if hasattr(scope, "target") else str(scope)


def _scope_rules(scope: object) -> list:
    if isinstance(scope, tuple):
        return list(scope[1]) if len(scope) > 1 else []
    return list(getattr(scope, "selector_rules", []))


class AccountKeychain:
    """Typed call builders for the AccountKeychain precompile.

    All methods return :class:`~tempo.models.Call` objects — pure calldata,
    no Web3 instance needed.
    """

    ADDRESS = ACCOUNT_KEYCHAIN_ADDRESS

    @staticmethod
    def authorize_key(
        *,
        key_id: str,
        signature_type: int,
        expiry: int = 0,
        enforce_limits: bool = False,
        limits: list | None = None,
        allow_any_calls: bool = True,
        allowed_calls: list | None = None,
    ) -> Call:
        """Build an ``authorizeKey`` call.

        Args:
            key_id: Access key address.
            signature_type: 0 (secp256k1), 1 (P256), or 2 (WebAuthn).
            expiry: Unix timestamp when the key expires (0 = never).
            enforce_limits: Whether to enforce token spending limits.
            limits: List of ``(token, amount, period)`` tuples.
            allow_any_calls: Allow unrestricted calls (default True).
            allowed_calls: List of ``(target, [(selector, [recipients]), ...])``.

        Returns:
            A ``Call`` targeting the AccountKeychain precompile.
        """
        restrictions = _build_restrictions_tuple(
            expiry=expiry,
            enforce_limits=enforce_limits,
            limits=limits,
            allow_any_calls=allow_any_calls,
            allowed_calls=allowed_calls,
        )
        data = ACCOUNT_KEYCHAIN.fns.authorizeKey(key_id, signature_type, restrictions).data
        return Call.create(to=ACCOUNT_KEYCHAIN_ADDRESS, data=data)

    @staticmethod
    def authorize_key_with_witness(
        *,
        key_id: str,
        signature_type: int,
        expiry: int = 0,
        enforce_limits: bool = False,
        limits: list | None = None,
        allow_any_calls: bool = True,
        allowed_calls: list | None = None,
        witness: bytes,
    ) -> Call:
        """Build an ``authorizeKey`` call with a TIP-1053 witness proof.

        The witness is a root-account signature over the authorization payload.
        Uses the ``authorizeKey(address,uint8,KeyRestrictions,bytes32)`` overload.
        """
        restrictions = _build_restrictions_tuple(
            expiry=expiry,
            enforce_limits=enforce_limits,
            limits=limits,
            allow_any_calls=allow_any_calls,
            allowed_calls=allowed_calls,
        )
        # Use the 4-overload authorizeKey via the witness selector
        data = ACCOUNT_KEYCHAIN.fns.authorizeKey(key_id, signature_type, restrictions, witness).data
        return Call.create(to=ACCOUNT_KEYCHAIN_ADDRESS, data=data)

    @staticmethod
    def revoke_key(*, key_id: str) -> Call:
        """Build a ``revokeKey(address)`` call."""
        data = ACCOUNT_KEYCHAIN.fns.revokeKey(key_id).data
        return Call.create(to=ACCOUNT_KEYCHAIN_ADDRESS, data=data)

    @staticmethod
    def update_spending_limit(*, key_id: str, token: str, new_limit: int) -> Call:
        """Build an ``updateSpendingLimit(address,address,uint256)`` call."""
        data = ACCOUNT_KEYCHAIN.fns.updateSpendingLimit(key_id, token, new_limit).data
        return Call.create(to=ACCOUNT_KEYCHAIN_ADDRESS, data=data)

    @staticmethod
    def set_allowed_calls(
        *,
        key_id: str,
        scopes: list,
    ) -> Call:
        """Build a ``setAllowedCalls(address,CallScope[])`` call.

        *scopes* is a list of ``(target, [(selector_bytes4, [recipient_addrs])])``.

        Example::

            call = AccountKeychain.set_allowed_calls(
                key_id=key_addr,
                scopes=[(token_addr, [(b"\\\\xa9\\\\x05\\\\x9c\\\\xbb", [])])],
            )
        """
        data = ACCOUNT_KEYCHAIN.fns.setAllowedCalls(key_id, scopes).data
        return Call.create(to=ACCOUNT_KEYCHAIN_ADDRESS, data=data)

    @staticmethod
    def remove_allowed_calls(*, key_id: str, target: str) -> Call:
        """Build a ``removeAllowedCalls(address,address)`` call."""
        data = ACCOUNT_KEYCHAIN.fns.removeAllowedCalls(key_id, target).data
        return Call.create(to=ACCOUNT_KEYCHAIN_ADDRESS, data=data)

    @staticmethod
    def authorize_admin_key(*, key_id: str, signature_type: int, witness: bytes) -> Call:
        """Build an ``authorizeAdminKey(address,uint8,bytes32)`` call (T6)."""
        data = ACCOUNT_KEYCHAIN.fns.authorizeAdminKey(key_id, signature_type, witness).data
        return Call.create(to=ACCOUNT_KEYCHAIN_ADDRESS, data=data)

    @staticmethod
    def burn_key_auth_witness(*, witness: bytes) -> Call:
        """Build a ``burnKeyAuthorizationWitness(bytes32)`` call."""
        data = ACCOUNT_KEYCHAIN.fns.burnKeyAuthorizationWitness(witness).data
        return Call.create(to=ACCOUNT_KEYCHAIN_ADDRESS, data=data)

    @staticmethod
    def is_admin_key(w3: Web3, *, account: str, key_id: str) -> bool:
        """Query ``isAdminKey(address,address)`` (read-only, needs Web3)."""
        fn = ACCOUNT_KEYCHAIN.fns.isAdminKey(account, key_id)
        raw = w3.eth.call({"to": ACCOUNT_KEYCHAIN_ADDRESS, "data": fn.data})
        return fn.decode(raw)

    @staticmethod
    def is_key_auth_witness_burned(w3: Web3, *, account: str, witness: bytes) -> bool:
        """Query ``isKeyAuthorizationWitnessBurned(address,bytes32)`` (read-only)."""
        fn = ACCOUNT_KEYCHAIN.fns.isKeyAuthorizationWitnessBurned(account, witness)
        raw = w3.eth.call({"to": ACCOUNT_KEYCHAIN_ADDRESS, "data": fn.data})
        return fn.decode(raw)
