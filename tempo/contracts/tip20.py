"""Typed helpers for TIP-20 token interactions, built on eth-contract.

Uses ``Contract.from_abi()`` with human-readable ABI strings for
Web3-agnostic calldata building — pure functions, no Web3 instance needed.
"""

from eth_contract import Contract


# ---------------------------------------------------------------------------
# Contract definition: human-readable ABI
# ---------------------------------------------------------------------------
TIP20 = Contract.from_abi([
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
    # Events
    "event Transfer(address indexed from, address indexed to, uint256 amount)",
    "event TransferWithMemo(address indexed from, address indexed to, uint256 amount, bytes32 memo)",
    "event Approval(address indexed owner, address indexed spender, uint256 amount)",
])



TIP20_ROLES = Contract.from_abi([
    "function grantRole(bytes32 role, address account)",
    "function revokeRole(bytes32 role, address account)",
    "function renounceRole(bytes32 role)",
    "function setRoleAdmin(bytes32 role, bytes32 adminRole)",
    "function hasRole(address account, bytes32 role) view returns (bool)",
    "function getRoleAdmin(bytes32 role) view returns (bytes32)",
])

