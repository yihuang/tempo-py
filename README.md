# tempo-py

Tempo blockchain Python SDK. Built on [web3.py](https://web3py.readthedocs.io/) and [eth-contract](https://github.com/yihuang/eth-contract).

Pure functions + data model classes, provider-agnostic calldata building.

## Quick start

```python
from tempo import TempoTransaction, Call, Signer, Builder
from tempo.constants import CHAIN_ID_MODERATO, ALPHA_USD
from tempo.transaction import sign_transaction, serialize, encode_tip20_transfer

# Build a transaction
tx = TempoTransaction.create(
    chain_id=CHAIN_ID_MODERATO,
    gas_limit=100_000,
    max_fee_per_gas=2_000_000_000,
    calls=(Call.create(to=ALPHA_USD, data=encode_tip20_transfer(to, amount)),),
)

# Sign and serialize
signed = sign_transaction(tx, Signer("0x..."))
hex_tx = serialize(signed)  # "0x76..."

# Send via web3.py
# w3.eth.send_raw_transaction(hex_tx)
```

## Modules

| Module | Description |
|---|---|
| `tempo.models` | `Call`, `Signature`, `TempoTransaction` — attrs frozen data models |
| `tempo.signer` | `Signer`, `recover_address`, `verify_signature` |
| `tempo.transaction` | RLP serialization, signing, fee sponsorship, Builder, TIP-20 encoding |
| `tempo.client` | JSON-RPC client (`send_raw_transaction`, `get_nonce`, …) |
| `tempo.keychain` | Access key models (`KeyAuthorization`, `KeyRestrictions`, `CallScope`, …) |
| `tempo.contracts` | `Contract.from_abi()` instances + typed wrappers for TIP20, AccountKeychain |

## eth-contract style

Contracts are defined with human-readable ABIs and shared globally. Calldata building is a pure function — no `Web3` instance required.

```python
from tempo.contracts import TIP20_CONTRACT, ACCOUNT_KEYCHAIN_CONTRACT

data = TIP20_CONTRACT.fns.transfer(to, amount).data                     # pure bytes
data = ACCOUNT_KEYCHAIN_CONTRACT.fns.revokeKey(key_id).data             # pure bytes
data = ACCOUNT_KEYCHAIN_CONTRACT.fns.authorizeKey(id, t,                # with struct tuple
    (0, False, [], True, [])).data
```

Or via typed wrappers with keyword arguments:

```python
from tempo.contracts import TIP20, AccountKeychain

alpha = TIP20(ALPHA_USD)
alpha.transfer(to=recipient, amount=10**18)  # -> Call

AccountKeychain.revoke_key(key_id=key_id)    # -> Call
```

## References

- [Tempo docs](https://docs.tempo.xyz)
- [Tempo Go SDK](https://github.com/tempoxyz/tempo-go)
- [eth-contract](https://github.com/yihuang/eth-contract)
