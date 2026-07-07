# tempo-py

Tempo blockchain Python SDK. Built on [web3.py](https://web3py.readthedocs.io/) and [eth-contract](https://github.com/yihuang/eth-contract).

Pure functions + data model classes, provider-agnostic calldata building.

## Quick start

```python
from tempo import TempoTransaction, Call, Signer, Builder
from tempo.constants import CHAIN_ID_MODERATO, ALPHA_USD
from tempo.transaction import sign_transaction, serialize
from tempo.contracts import TIP20

tx = TempoTransaction.create(
    chain_id=CHAIN_ID_MODERATO,
    gas_limit=100_000,
    max_fee_per_gas=2_000_000_000,
    calls=(Call.create(to=ALPHA_USD, data=TIP20.fns.transfer(to, amount).data),),
)
signed = sign_transaction(tx, Signer("0x..."))
hex_tx = serialize(signed)  # "0x76..."
```

## Modules

| Module | Description |
|---|---|
| `tempo.models` | `Call`, `Signature`, `TempoTransaction` — attrs frozen data models |
| `tempo.signer` | `Signer`, `recover_address`, `verify_signature` |
| `tempo.transaction` | RLP serialization, signing, fee sponsorship, Builder |
| `tempo.client` | JSON-RPC client (`send_raw_transaction`, `get_nonce`, …) |
| `tempo.keychain` | Access key models (`KeyAuthorization`, `KeyRestrictions`, `CallScope`, …) |
| `tempo.contracts` | `Contract.from_abi()` instances + typed wrappers |
| `tempo.devnet` | Multi-node testnet generator + supervisord management (like pystarport) |

## eth-contract style

Contracts defined with human-readable ABIs, shared globally. Calldata is a pure function — no `Web3` instance required.

```python
from tempo.contracts import TIP20, ACCOUNT_KEYCHAIN

data = TIP20.fns.transfer(to, amount).data                 # pure bytes
data = ACCOUNT_KEYCHAIN.fns.revokeKey(key_id).data         # pure bytes
data = ACCOUNT_KEYCHAIN.fns.authorizeKey(id, sig_type,
    (0, False, [], True, [])).data                         # with struct tuple
```



## Devnet (Multi-Node Testnet)

Generate and run a multi-node Tempo testnet from a single YAML config.

```bash
# Install devnet dependencies
uv sync --extra devnet

# Create a config (see devnet.yaml.example for all options)
cat > devnet.yaml <<EOF
chain_id: 1337
accounts: 100
epoch_length: 100
gas_limit: 500000000
seed: 42
validators:
  - host: 127.0.0.1
    port: 8000
    moniker: node0
  - host: 127.0.0.1
    port: 8010
    moniker: node1
  - host: 127.0.0.1
    port: 8020
    moniker: node2
  - host: 127.0.0.1
    port: 8030
    moniker: node3
EOF

# Init + start in one command
tempo-devnet serve --data ./data --config ./devnet.yaml

# Or step by step:
tempo-devnet init --data ./data --config ./devnet.yaml
tempo-devnet start --data ./data

# Check status
tempo-devnet status --data ./data

# Supervisor control
tempo-devnet supervisorctl status
tempo-devnet supervisorctl tail node-1
```

### Port Scheme

Each node gets a ``base_port`` (from config). Service ports are derived:

| Role              | Offset | Flag                              |
|-------------------|--------|------------------------------------|
| Consensus P2P     | +0     | ``--consensus.listen-address``    |
| Execution P2P     | +1     | ``--port / --discovery.port``     |
| Consensus Metrics | +2     | ``--consensus.metrics-address``   |
| Engine API        | +3     | ``--authrpc.port``                |
| HTTP JSON-RPC     | +4     | ``--http.port``                    |
| WebSocket         | +5     | ``--ws.port``                      |

### Requirements

The ``tempo`` and ``tempo-xtask`` binaries must be installed and on PATH:

```bash
# Install from source
cargo build --profile profiling --bin tempo --bin tempo-xtask
# Or download from https://docs.tempo.xyz/guide/node/installation
```

## References

- [Tempo docs](https://docs.tempo.xyz)
- [Tempo Go SDK](https://github.com/tempoxyz/tempo-go)
- [Tempo Devnet ops docs](https://github.com/tempoxyz/tempo/blob/main/docs/operations.md)
- [eth-contract](https://github.com/yihuang/eth-contract)
- [pystarport](https://github.com/crypto-com/pystarport) — inspiration
