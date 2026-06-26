"""Tests for tempo.contracts — eth-contract based typed call builders.

All selector assertions are derived from the Contract instances themselves,
never hardcoded. This ensures tests stay in sync with the ABI definitions.
"""

import pytest
from tempo.contracts import (
    TIP20_CONTRACT, TIP20_ROLES_CONTRACT,
    ACCOUNT_KEYCHAIN_CONTRACT,
    TIP20, AccountKeychain,
    ALPHA_USD, ACCOUNT_KEYCHAIN_ADDRESS,
)
from tempo.models import Call

RECIPIENT = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
KEY_ID = "0xaaaaaaaa00000000000000000000000000000000"


# ---------------------------------------------------------------------------
# TIP20_CONTRACT — direct eth-contract usage
# ---------------------------------------------------------------------------

class TestTIP20ContractDirect:
    def test_transfer_calldata(self):
        data = TIP20_CONTRACT.fns.transfer(RECIPIENT, 10**18).data
        assert data[:4] == bytes(TIP20_CONTRACT.fns.transfer.selector)
        assert len(data) == 68

    def test_approve_calldata(self):
        data = TIP20_CONTRACT.fns.approve(RECIPIENT, 10**18).data
        assert data[:4] == bytes(TIP20_CONTRACT.fns.approve.selector)

    def test_transfer_with_memo(self):
        data = TIP20_CONTRACT.fns.transferWithMemo(RECIPIENT, 10**18, b"\x00" * 32).data
        assert data[:4] == bytes(TIP20_CONTRACT.fns.transferWithMemo.selector)

    def test_burn_calldata(self):
        data = TIP20_CONTRACT.fns.burn(1000).data
        assert data[:4] == bytes(TIP20_CONTRACT.fns.burn.selector)

    def test_mint_calldata(self):
        data = TIP20_CONTRACT.fns.mint(RECIPIENT, 10**18).data
        assert data[:4] == bytes(TIP20_CONTRACT.fns.mint.selector)

    def test_pause_unpause(self):
        assert TIP20_CONTRACT.fns.pause().data[:4] == bytes(TIP20_CONTRACT.fns.pause.selector)
        assert TIP20_CONTRACT.fns.unpause().data[:4] == bytes(TIP20_CONTRACT.fns.unpause.selector)


class TestTIP20RolesContract:
    def test_grant_role(self):
        role = b"\x00" * 32
        data = TIP20_ROLES_CONTRACT.fns.grantRole(role, RECIPIENT).data
        assert data[:4] == bytes(TIP20_ROLES_CONTRACT.fns.grantRole.selector)

    def test_revoke_role(self):
        role = b"\x00" * 32
        data = TIP20_ROLES_CONTRACT.fns.revokeRole(role, RECIPIENT).data
        assert data[:4] == bytes(TIP20_ROLES_CONTRACT.fns.revokeRole.selector)

    def test_renounce_role(self):
        role = b"\x00" * 32
        data = TIP20_ROLES_CONTRACT.fns.renounceRole(role).data
        assert data[:4] == bytes(TIP20_ROLES_CONTRACT.fns.renounceRole.selector)


# ---------------------------------------------------------------------------
# TIP20 class — typed wrapper
# ---------------------------------------------------------------------------

class TestTIP20Wrapper:
    def test_transfer(self):
        alpha = TIP20(ALPHA_USD)
        call = alpha.transfer(to=RECIPIENT, amount=10**18)
        assert isinstance(call, Call)
        assert call.data[:4] == bytes(TIP20_CONTRACT.fns.transfer.selector)

    def test_approve(self):
        call = TIP20(ALPHA_USD).approve(spender=RECIPIENT, amount=10**18)
        assert call.data[:4] == bytes(TIP20_CONTRACT.fns.approve.selector)

    def test_transfer_with_memo(self):
        call = TIP20(ALPHA_USD).transfer_with_memo(
            to=RECIPIENT, amount=10**18, memo=b"\x01" * 32,
        )
        assert call.data[:4] == bytes(TIP20_CONTRACT.fns.transferWithMemo.selector)
        assert len(call.data) == 100

    def test_transfer_from(self):
        call = TIP20(ALPHA_USD).transfer_from(
            sender="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
            to=RECIPIENT, amount=10**18,
        )
        assert call.data[:4] == bytes(TIP20_CONTRACT.fns.transferFrom.selector)

    def test_burn(self):
        call = TIP20(ALPHA_USD).burn(amount=1000)
        assert call.data[:4] == bytes(TIP20_CONTRACT.fns.burn.selector)

    def test_mint(self):
        call = TIP20(ALPHA_USD).mint(to=RECIPIENT, amount=10**18)
        assert call.data[:4] == bytes(TIP20_CONTRACT.fns.mint.selector)

    def test_pause_unpause(self):
        alpha = TIP20(ALPHA_USD)
        assert alpha.pause().data[:4] == bytes(TIP20_CONTRACT.fns.pause.selector)
        assert alpha.unpause().data[:4] == bytes(TIP20_CONTRACT.fns.unpause.selector)

    def test_claim_rewards(self):
        call = TIP20(ALPHA_USD).claim_rewards()
        assert call.data[:4] == bytes(TIP20_CONTRACT.fns.claimRewards.selector)

    def test_set_supply_cap(self):
        call = TIP20(ALPHA_USD).set_supply_cap(new_supply_cap=1_000_000_000)
        assert call.data[:4] == bytes(TIP20_CONTRACT.fns.setSupplyCap.selector)

    def test_change_transfer_policy_id(self):
        call = TIP20(ALPHA_USD).change_transfer_policy_id(new_policy_id=1)
        assert call.data[:4] == bytes(TIP20_CONTRACT.fns.changeTransferPolicyId.selector)

    def test_role_management(self):
        alpha = TIP20(ALPHA_USD)
        role = b"\x00" * 32
        c1 = alpha.grant_role(role=role, account=RECIPIENT)
        assert c1.data[:4] == bytes(TIP20_ROLES_CONTRACT.fns.grantRole.selector)
        c2 = alpha.revoke_role(role=role, account=RECIPIENT)
        assert c2.data[:4] == bytes(TIP20_ROLES_CONTRACT.fns.revokeRole.selector)
        c3 = alpha.renounce_role(role=role)
        assert c3.data[:4] == bytes(TIP20_ROLES_CONTRACT.fns.renounceRole.selector)

    def test_permit(self):
        call = TIP20(ALPHA_USD).permit(
            owner=RECIPIENT,
            spender="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
            value=100, deadline=9999999,
            v=27, r=b"\x01" * 32, s=b"\x02" * 32,
        )
        assert call.data[:4] == bytes(TIP20_CONTRACT.fns.permit.selector)


# ---------------------------------------------------------------------------
# ACCOUNT_KEYCHAIN_CONTRACT — direct eth-contract usage
# ---------------------------------------------------------------------------

class TestAccountKeychainDirect:
    def test_revoke_key(self):
        data = ACCOUNT_KEYCHAIN_CONTRACT.fns.revokeKey(KEY_ID).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.revokeKey.selector)
        assert len(data) == 36

    def test_authorize_key_simple(self):
        data = ACCOUNT_KEYCHAIN_CONTRACT.fns.authorizeKey(
            KEY_ID, 0, (0, False, [], True, []),
        ).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.authorizeKey.selector)

    def test_authorize_key_with_limits(self):
        data = ACCOUNT_KEYCHAIN_CONTRACT.fns.authorizeKey(
            KEY_ID, 0,
            (1893456000, True, [(ALPHA_USD, 10**18, 86400)], False, []),
        ).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.authorizeKey.selector)

    def test_update_spending_limit(self):
        data = ACCOUNT_KEYCHAIN_CONTRACT.fns.updateSpendingLimit(
            KEY_ID, ALPHA_USD, 1_000_000,
        ).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.updateSpendingLimit.selector)

    def test_authorize_admin_key(self):
        data = ACCOUNT_KEYCHAIN_CONTRACT.fns.authorizeAdminKey(
            KEY_ID, 0, b"\x00" * 32,
        ).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.authorizeAdminKey.selector)

    def test_burn_key_auth_witness(self):
        data = ACCOUNT_KEYCHAIN_CONTRACT.fns.burnKeyAuthorizationWitness(b"\x00" * 32).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.burnKeyAuthorizationWitness.selector)

    def test_set_allowed_calls(self):
        data = ACCOUNT_KEYCHAIN_CONTRACT.fns.setAllowedCalls(
            KEY_ID,
            [(ALPHA_USD, [(bytes.fromhex("a9059cbb"), [RECIPIENT])])],
        ).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.setAllowedCalls.selector)

    def test_remove_allowed_calls(self):
        data = ACCOUNT_KEYCHAIN_CONTRACT.fns.removeAllowedCalls(KEY_ID, ALPHA_USD).data
        assert data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.removeAllowedCalls.selector)


# ---------------------------------------------------------------------------
# AccountKeychain class — typed wrapper
# ---------------------------------------------------------------------------

class TestAccountKeychainWrapper:
    def test_authorize_key(self):
        call = AccountKeychain.authorize_key(key_id=KEY_ID, signature_type=0)
        assert isinstance(call, Call)
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.authorizeKey.selector)

    def test_authorize_key_with_limits(self):
        call = AccountKeychain.authorize_key(
            key_id=KEY_ID, signature_type=0,
            expiry=1893456000, enforce_limits=True,
            limits=[(ALPHA_USD, 10**18, 86400)],
            allow_any_calls=False,
        )
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.authorizeKey.selector)

    def test_revoke_key(self):
        call = AccountKeychain.revoke_key(key_id=KEY_ID)
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.revokeKey.selector)

    def test_update_spending_limit(self):
        call = AccountKeychain.update_spending_limit(
            key_id=KEY_ID, token=ALPHA_USD, new_limit=1_000_000,
        )
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.updateSpendingLimit.selector)

    def test_authorize_admin_key(self):
        call = AccountKeychain.authorize_admin_key(
            key_id=KEY_ID, signature_type=0, witness=b"\x00" * 32,
        )
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.authorizeAdminKey.selector)

    def test_set_allowed_calls(self):
        call = AccountKeychain.set_allowed_calls(
            key_id=KEY_ID,
            scopes=[(ALPHA_USD, [(bytes.fromhex("a9059cbb"), [RECIPIENT])])],
        )
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.setAllowedCalls.selector)

    def test_remove_allowed_calls(self):
        call = AccountKeychain.remove_allowed_calls(key_id=KEY_ID, target=ALPHA_USD)
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.removeAllowedCalls.selector)

    def test_burn_key_auth_witness(self):
        call = AccountKeychain.burn_key_auth_witness(witness=b"\x00" * 32)
        assert call.data[:4] == bytes(ACCOUNT_KEYCHAIN_CONTRACT.fns.burnKeyAuthorizationWitness.selector)

    def test_address_constant(self):
        assert AccountKeychain.ADDRESS == ACCOUNT_KEYCHAIN_ADDRESS


# ---------------------------------------------------------------------------
# Cross-module consistency
# ---------------------------------------------------------------------------

class TestContractConsistency:
    def test_tip20_selector_consistency(self):
        from tempo.transaction import TIP20_TRANSFER_SELECTOR
        assert bytes(TIP20_CONTRACT.fns.transfer.selector) == TIP20_TRANSFER_SELECTOR

    def test_instance_sharing(self):
        from tempo.contracts.tip20 import TIP20_CONTRACT as C1
        from tempo.contracts import TIP20_CONTRACT as C2
        assert C1 is C2

    def test_keychain_instance_sharing(self):
        from tempo.contracts.keychain import ACCOUNT_KEYCHAIN_CONTRACT as C1
        from tempo.contracts import ACCOUNT_KEYCHAIN_CONTRACT as C2
        assert C1 is C2
