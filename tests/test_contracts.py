"""Tests for tempo.contracts — eth-contract based typed call builders.

All selector assertions are derived from the Contract instances themselves,
never hardcoded. This ensures tests stay in sync with the ABI definitions.
"""

from tempo.contracts import (
    ACCOUNT_KEYCHAIN,
    ACCOUNT_KEYCHAIN_ADDRESS,
    ALPHA_USD,
    TIP20,
    TIP20_ROLES,
    AccountKeychain,
)
from tempo.contracts.keychain import ACCOUNT_KEYCHAIN as ACCOUNT_KEYCHAIN_VIA_KEYCHAIN
from tempo.contracts.tip20 import TIP20 as TIP20_VIA_TIP20
from tempo.keychain import CallScope
from tempo.models import Call

RECIPIENT = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
KEY_ID = "0xaaaaaaaa00000000000000000000000000000000"


# ---------------------------------------------------------------------------
# TIP20_CONTRACT — direct eth-contract usage
# ---------------------------------------------------------------------------


class TestTIP20ContractDirect:
    def test_transfer_calldata(self):
        data = TIP20.fns.transfer(RECIPIENT, 10**18).data
        assert data[:4] == bytes(TIP20.fns.transfer.selector)
        assert len(data) == 68

    def test_approve_calldata(self):
        data = TIP20.fns.approve(RECIPIENT, 10**18).data
        assert data[:4] == bytes(TIP20.fns.approve.selector)

    def test_transfer_with_memo(self):
        data = TIP20.fns.transferWithMemo(RECIPIENT, 10**18, b"\x00" * 32).data
        assert data[:4] == bytes(TIP20.fns.transferWithMemo.selector)

    def test_burn_calldata(self):
        data = TIP20.fns.burn(1000).data
        assert data[:4] == bytes(TIP20.fns.burn.selector)

    def test_mint_calldata(self):
        data = TIP20.fns.mint(RECIPIENT, 10**18).data
        assert data[:4] == bytes(TIP20.fns.mint.selector)

    def test_pause_unpause(self):
        assert TIP20.fns.pause().data[:4] == bytes(TIP20.fns.pause.selector)
        assert TIP20.fns.unpause().data[:4] == bytes(TIP20.fns.unpause.selector)


class TestTIP20RolesContract:
    def test_grant_role(self):
        role = b"\x00" * 32
        data = TIP20_ROLES.fns.grantRole(role, RECIPIENT).data
        assert data[:4] == bytes(TIP20_ROLES.fns.grantRole.selector)

    def test_revoke_role(self):
        role = b"\x00" * 32
        data = TIP20_ROLES.fns.revokeRole(role, RECIPIENT).data
        assert data[:4] == bytes(TIP20_ROLES.fns.revokeRole.selector)

    def test_renounce_role(self):
        role = b"\x00" * 32
        data = TIP20_ROLES.fns.renounceRole(role).data
        assert data[:4] == bytes(TIP20_ROLES.fns.renounceRole.selector)


# ---------------------------------------------------------------------------
# ACCOUNT_KEYCHAIN — direct eth-contract usage
# ---------------------------------------------------------------------------


class TestAccountKeychainDirect:
    def test_revoke_key(self):
        data = ACCOUNT_KEYCHAIN.fns.revokeKey(KEY_ID).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.revokeKey.selector)
        assert len(data) == 36

    def test_authorize_key_simple(self):
        data = ACCOUNT_KEYCHAIN.fns.authorizeKey(
            KEY_ID,
            0,
            (0, False, [], True, []),
        ).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.authorizeKey.selector)

    def test_authorize_key_with_limits(self):
        data = ACCOUNT_KEYCHAIN.fns.authorizeKey(
            KEY_ID,
            0,
            (1893456000, True, [(ALPHA_USD, 10**18, 86400)], False, []),
        ).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.authorizeKey.selector)

    def test_update_spending_limit(self):
        data = ACCOUNT_KEYCHAIN.fns.updateSpendingLimit(
            KEY_ID,
            ALPHA_USD,
            1_000_000,
        ).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.updateSpendingLimit.selector)

    def test_authorize_admin_key(self):
        data = ACCOUNT_KEYCHAIN.fns.authorizeAdminKey(
            KEY_ID,
            0,
            b"\x00" * 32,
        ).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.authorizeAdminKey.selector)

    def test_burn_key_auth_witness(self):
        data = ACCOUNT_KEYCHAIN.fns.burnKeyAuthorizationWitness(b"\x00" * 32).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.burnKeyAuthorizationWitness.selector)

    def test_set_allowed_calls(self):
        data = ACCOUNT_KEYCHAIN.fns.setAllowedCalls(
            KEY_ID,
            [(ALPHA_USD, [(bytes.fromhex("a9059cbb"), [RECIPIENT])])],
        ).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.setAllowedCalls.selector)

    def test_remove_allowed_calls(self):
        data = ACCOUNT_KEYCHAIN.fns.removeAllowedCalls(KEY_ID, ALPHA_USD).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.removeAllowedCalls.selector)


# ---------------------------------------------------------------------------
# AccountKeychain class — typed wrapper
# ---------------------------------------------------------------------------


class TestAccountKeychainWrapper:
    def test_authorize_key(self):
        call = AccountKeychain.authorize_key(key_id=KEY_ID, signature_type=0)
        assert isinstance(call, Call)
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.authorizeKey.selector)

    def test_authorize_key_with_limits(self):
        call = AccountKeychain.authorize_key(
            key_id=KEY_ID,
            signature_type=0,
            expiry=1893456000,
            enforce_limits=True,
            limits=[(ALPHA_USD, 10**18, 86400)],
            allow_any_calls=False,
        )
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.authorizeKey.selector)

    def test_revoke_key(self):
        call = AccountKeychain.revoke_key(key_id=KEY_ID)
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.revokeKey.selector)

    def test_update_spending_limit(self):
        call = AccountKeychain.update_spending_limit(
            key_id=KEY_ID,
            token=ALPHA_USD,
            new_limit=1_000_000,
        )
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.updateSpendingLimit.selector)

    def test_authorize_admin_key(self):
        call = AccountKeychain.authorize_admin_key(
            key_id=KEY_ID,
            signature_type=0,
            witness=b"\x00" * 32,
        )
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.authorizeAdminKey.selector)

    def test_set_allowed_calls(self):
        call = AccountKeychain.set_allowed_calls(
            key_id=KEY_ID,
            scopes=[(ALPHA_USD, [(bytes.fromhex("a9059cbb"), [RECIPIENT])])],
        )
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.setAllowedCalls.selector)

    def test_remove_allowed_calls(self):
        call = AccountKeychain.remove_allowed_calls(key_id=KEY_ID, target=ALPHA_USD)
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.removeAllowedCalls.selector)

    def test_burn_key_auth_witness(self):
        call = AccountKeychain.burn_key_auth_witness(witness=b"\x00" * 32)
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN.fns.burnKeyAuthorizationWitness.selector)

    def test_address_constant(self):
        assert AccountKeychain.ADDRESS == ACCOUNT_KEYCHAIN_ADDRESS


# ---------------------------------------------------------------------------
# Cross-module consistency
# ---------------------------------------------------------------------------


class TestContractConsistency:
    def test_tip20_selector_consistency(self):
        cs = CallScope.transfer("0x" + "20" * 20)
        assert cs.selector == bytes(TIP20.fns.transfer.selector)

    def test_instance_sharing(self):
        assert TIP20 is TIP20_VIA_TIP20

    def test_keychain_instance_sharing(self):
        assert ACCOUNT_KEYCHAIN is ACCOUNT_KEYCHAIN_VIA_KEYCHAIN
