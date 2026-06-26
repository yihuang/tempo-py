"""Typed helpers for TIP-20 token interactions, built on eth-contract.

Uses ``Contract.from_abi()`` with human-readable ABI strings for
Web3-agnostic calldata building — pure functions, no Web3 instance needed.
"""

from eth_contract import Contract

from ..models import Call

# ---------------------------------------------------------------------------
# Contract definition: human-readable ABI
# ---------------------------------------------------------------------------
TIP20_CONTRACT = Contract.from_abi([
    "function transfer(address to, uint256 amount) returns (bool)",
    "function transferWithMemo(address to, uint256 amount, bytes32 memo) returns (bool)",
    "function transferFrom(address sender, address to, uint256 amount) returns (bool)",
    "function transferFromWithMemo(address sender, address to, uint256 amount, bytes32 memo) returns (bool)",
    "function approve(address spender, uint256 amount) returns (bool)",
    "function mint(address to, uint256 amount) returns (bool)",
    "function mintWithMemo(address to, uint256 amount, bytes32 memo) returns (bool)",
    "function burn(uint256 amount) returns (bool)",
    "function burnWithMemo(uint256 amount, bytes32 memo) returns (bool)",
    "function burnBlocked(address sender, uint256 amount) returns (bool)",
    "function pause()",
    "function unpause()",
    "function setSupplyCap(uint256 newSupplyCap)",
    "function changeTransferPolicyId(uint64 newPolicyId)",
    "function claimRewards()",
    "function distributeReward(uint256 amount)",
    "function setLogoURI(string logoURI)",
    "function setNextQuoteToken(address newQuoteToken)",
    "function setRewardRecipient(address newRewardRecipient)",
    "function completeQuoteTokenUpdate()",
    "function permit(address owner, address spender, uint256 value, uint256 deadline, uint8 v, bytes32 r, bytes32 s)",
])

TIP20_ROLES_CONTRACT = Contract.from_abi([
    "function grantRole(bytes32 role, address account)",
    "function revokeRole(bytes32 role, address account)",
    "function renounceRole(bytes32 role)",
    "function setRoleAdmin(bytes32 role, bytes32 adminRole)",
    "function hasRole(address account, bytes32 role) view returns (bool)",
    "function getRoleAdmin(bytes32 role) view returns (bytes32)",
])


def _memo32(memo: bytes) -> bytes:
    """Right-pad memo to 32 bytes (bytes32 padding convention)."""
    if len(memo) > 32:
        raise ValueError("memo must be at most 32 bytes")
    return memo.ljust(32, b"\x00")


class TIP20:
    """TIP-20 token call builders, built on eth-contract.

    Instantiate with a token address, then call methods to build ``Call``
    objects — pure calldata, no Web3 instance needed::

        alpha = TIP20(ALPHA_USD)
        call = alpha.transfer(to=recipient, amount=100_000_000)

    Read-only queries (``has_role``, ``get_role_admin``) need a Web3
    provider — these are the exception.
    """

    def __init__(self, token: str) -> None:
        self.token = token

    # -- Mutations (return Call) ------------------------------------------

    def transfer(self, *, to: str, amount: int) -> Call:
        """Build a ``transfer(address,uint256)`` call."""
        data = TIP20_CONTRACT.fns.transfer(to, amount).data
        return Call.create(to=self.token, data=data)

    def transfer_with_memo(self, *, to: str, amount: int, memo: bytes) -> Call:
        """Build a ``transferWithMemo(address,uint256,bytes32)`` call."""
        data = TIP20_CONTRACT.fns.transferWithMemo(to, amount, _memo32(memo)).data
        return Call.create(to=self.token, data=data)

    def transfer_from(self, *, sender: str, to: str, amount: int) -> Call:
        """Build a ``transferFrom(address,address,uint256)`` call."""
        data = TIP20_CONTRACT.fns.transferFrom(sender, to, amount).data
        return Call.create(to=self.token, data=data)

    def transfer_from_with_memo(
        self, *, sender: str, to: str, amount: int, memo: bytes
    ) -> Call:
        """Build a ``transferFromWithMemo(address,address,uint256,bytes32)`` call."""
        data = TIP20_CONTRACT.fns.transferFromWithMemo(sender, to, amount, _memo32(memo)).data
        return Call.create(to=self.token, data=data)

    def approve(self, *, spender: str, amount: int) -> Call:
        """Build an ``approve(address,uint256)`` call."""
        data = TIP20_CONTRACT.fns.approve(spender, amount).data
        return Call.create(to=self.token, data=data)

    def mint(self, *, to: str, amount: int) -> Call:
        """Build a ``mint(address,uint256)`` call (issuer only)."""
        data = TIP20_CONTRACT.fns.mint(to, amount).data
        return Call.create(to=self.token, data=data)

    def mint_with_memo(self, *, to: str, amount: int, memo: bytes) -> Call:
        """Build a ``mintWithMemo(address,uint256,bytes32)`` call (issuer only)."""
        data = TIP20_CONTRACT.fns.mintWithMemo(to, amount, _memo32(memo)).data
        return Call.create(to=self.token, data=data)

    def burn(self, *, amount: int) -> Call:
        """Build a ``burn(uint256)`` call."""
        data = TIP20_CONTRACT.fns.burn(amount).data
        return Call.create(to=self.token, data=data)

    def burn_with_memo(self, *, amount: int, memo: bytes) -> Call:
        """Build a ``burnWithMemo(uint256,bytes32)`` call."""
        data = TIP20_CONTRACT.fns.burnWithMemo(amount, _memo32(memo)).data
        return Call.create(to=self.token, data=data)

    def burn_blocked(self, *, sender: str, amount: int) -> Call:
        """Build a ``burnBlocked(address,uint256)`` call."""
        data = TIP20_CONTRACT.fns.burnBlocked(sender, amount).data
        return Call.create(to=self.token, data=data)

    def pause(self) -> Call:
        """Build a ``pause()`` call."""
        data = TIP20_CONTRACT.fns.pause().data
        return Call.create(to=self.token, data=data)

    def unpause(self) -> Call:
        """Build an ``unpause()`` call."""
        data = TIP20_CONTRACT.fns.unpause().data
        return Call.create(to=self.token, data=data)

    def set_supply_cap(self, *, new_supply_cap: int) -> Call:
        """Build a ``setSupplyCap(uint256)`` call."""
        data = TIP20_CONTRACT.fns.setSupplyCap(new_supply_cap).data
        return Call.create(to=self.token, data=data)

    def change_transfer_policy_id(self, *, new_policy_id: int) -> Call:
        """Build a ``changeTransferPolicyId(uint64)`` call."""
        data = TIP20_CONTRACT.fns.changeTransferPolicyId(new_policy_id).data
        return Call.create(to=self.token, data=data)

    def claim_rewards(self) -> Call:
        """Build a ``claimRewards()`` call."""
        data = TIP20_CONTRACT.fns.claimRewards().data
        return Call.create(to=self.token, data=data)

    def distribute_reward(self, *, amount: int) -> Call:
        """Build a ``distributeReward(uint256)`` call."""
        data = TIP20_CONTRACT.fns.distributeReward(amount).data
        return Call.create(to=self.token, data=data)

    def set_logo_uri(self, *, logo_uri: str) -> Call:
        """Build a ``setLogoURI(string)`` call."""
        data = TIP20_CONTRACT.fns.setLogoURI(logo_uri).data
        return Call.create(to=self.token, data=data)

    def set_next_quote_token(self, *, new_quote_token: str) -> Call:
        """Build a ``setNextQuoteToken(address)`` call."""
        data = TIP20_CONTRACT.fns.setNextQuoteToken(new_quote_token).data
        return Call.create(to=self.token, data=data)

    def set_reward_recipient(self, *, new_reward_recipient: str) -> Call:
        """Build a ``setRewardRecipient(address)`` call."""
        data = TIP20_CONTRACT.fns.setRewardRecipient(new_reward_recipient).data
        return Call.create(to=self.token, data=data)

    def complete_quote_token_update(self) -> Call:
        """Build a ``completeQuoteTokenUpdate()`` call."""
        data = TIP20_CONTRACT.fns.completeQuoteTokenUpdate().data
        return Call.create(to=self.token, data=data)

    def permit(
        self,
        *,
        owner: str,
        spender: str,
        value: int,
        deadline: int,
        v: int,
        r: bytes,
        s: bytes,
    ) -> Call:
        """Build a ``permit(address,address,uint256,uint256,uint8,bytes32,bytes32)`` call."""
        if len(r) != 32:
            raise ValueError("r must be exactly 32 bytes")
        if len(s) != 32:
            raise ValueError("s must be exactly 32 bytes")
        data = TIP20_CONTRACT.fns.permit(owner, spender, value, deadline, v, r, s).data
        return Call.create(to=self.token, data=data)

    # -- Role-management mutations ------------------------------------------

    def grant_role(self, *, role: bytes, account: str) -> Call:
        data = TIP20_ROLES_CONTRACT.fns.grantRole(role, account).data
        return Call.create(to=self.token, data=data)

    def revoke_role(self, *, role: bytes, account: str) -> Call:
        data = TIP20_ROLES_CONTRACT.fns.revokeRole(role, account).data
        return Call.create(to=self.token, data=data)

    def renounce_role(self, *, role: bytes) -> Call:
        data = TIP20_ROLES_CONTRACT.fns.renounceRole(role).data
        return Call.create(to=self.token, data=data)

    def set_role_admin(self, *, role: bytes, admin_role: bytes) -> Call:
        data = TIP20_ROLES_CONTRACT.fns.setRoleAdmin(role, admin_role).data
        return Call.create(to=self.token, data=data)

    # -- Read-only queries (need Web3 provider) ----------------------------

    def get_role_admin(self, w3, *, role: bytes) -> bytes:
        fn = TIP20_ROLES_CONTRACT.fns.getRoleAdmin(role)
        raw = w3.eth.call({"to": self.token, "data": fn.data})
        return fn.decode(raw)

    def has_role(self, w3, *, account: str, role: bytes) -> bool:
        fn = TIP20_ROLES_CONTRACT.fns.hasRole(role, account)
        raw = w3.eth.call({"to": self.token, "data": fn.data})
        return fn.decode(raw)
