# Devnet — Multi-Node Testnet

`tempo-devnet` generates and runs a multi-node Tempo testnet from a
single YAML config file.

## Quick Start

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
```

## Deployment Modes

The devnet supports two deployment modes:

- **Native (supervisord)** — each validator runs as an OS process managed
  by supervisord.  Use `tempo-devnet serve` / `start`.
- **Docker Compose** — each validator runs in an isolated container on a
  bridge network.  Use `--gen-compose-file` with `tempo-devnet init`.

---

## Native Mode

### Port Scheme

Each validator gets a ``base_port``.  Service ports are derived as offsets:

| Role              | Offset | CLI Flag                          |
|-------------------|--------|-----------------------------------|
| Consensus P2P     | +0     | ``--consensus.listen-address``    |
| Execution P2P     | +1     | ``--port`` / ``--discovery.port`` |
| Consensus Metrics | +2     | ``--consensus.metrics-address``   |
| Engine API        | +3     | ``--authrpc.port``                |
| HTTP JSON-RPC     | +4     | ``--http.port``                   |
| WebSocket         | +5     | ``--ws.port``                     |

### Network Topology

Each validator has three configurable addresses:

| Field        | Purpose                                    | Default       |
|--------------|--------------------------------------------|---------------|
| ``host``     | Advertised IP for trusted-peers + metrics  | ``127.0.0.1`` |
| ``p2p_host`` | P2P listen IP                              | same as ``host`` |
| ``rpc_host`` | RPC/WS listen IP                           | ``0.0.0.0``   |

Private validator network example:

```yaml
validators:
  - host: 127.0.0.1
    port: 8000
    p2p_host: 127.0.0.1   # private — only validators on same host
    rpc_host: 0.0.0.0     # public — external full nodes can follow via WS
```

External full nodes sync via ``--follow ws://HOST:WS_PORT``.

### Supervisor Control

```bash
# Check status
tempo-devnet status --data ./data

# Supervisor control
tempo-devnet supervisorctl status
tempo-devnet supervisorctl tail node0
tempo-devnet supervisorctl restart node0
```

### Logs

Supervisord captures stdout/stderr to per-node log files:

```
data/node0/node.log
data/node1/node.log
```

---

## Docker Compose Mode

Generate the cluster and start it with ``docker compose``:

```bash
# Generate genesis + docker-compose.yml
tempo-devnet init --data ./data --config ./devnet.yaml --gen-compose-file

# Start the cluster
docker compose -f ./data/docker-compose.yml up -d

# Tail logs
docker compose -f ./data/docker-compose.yml logs -f
```

### Internal Ports (Container)

All validators use the same fixed ports inside their containers (each
container has its own network namespace):

| Role              | Internal Port |
|-------------------|---------------|
| Consensus P2P     | 8000          |
| Execution P2P     | 8001          |
| Consensus Metrics | 8002          |
| Engine API        | 8003          |
| HTTP JSON-RPC     | 8004          |
| WebSocket         | 8005          |

### Published Ports (Host)

Host ports use the ``base_port`` offset scheme to avoid conflicts:

| Validator | HTTP RPC       | WebSocket      | Engine API             |
|-----------|----------------|----------------|------------------------|
| node0     | ``8004:8004``  | ``8005:8005``  | ``127.0.0.1:8003:8003`` |
| node1     | ``8014:8004``  | ``8015:8005``  | ``127.0.0.1:8013:8003`` |
| node2     | ``8024:8004``  | ``8025:8005``  | ``127.0.0.1:8023:8003`` |
| node3     | ``8034:8004``  | ``8035:8005``  | ``127.0.0.1:8033:8003`` |

### External Full Nodes

Full nodes can sync from any validator's published WebSocket port:

```bash
tempo node --follow ws://127.0.0.1:8005
```

### Docker Image

The default image is the official Tempo container.  Override in config:

```yaml
docker:
  image: ghcr.io/tempoxyz/tempo:latest
  network: tempo-devnet
```

---

## CLI Reference

```
tempo-devnet serve   --data ./data --config ./devnet.yaml  (init + start)
tempo-devnet init    --data ./data --config ./devnet.yaml  (genesis + keys)
tempo-devnet start   --data ./data                          (start supervisor)
tempo-devnet status  --data ./data                          (cluster summary)
tempo-devnet supervisorctl <args>                           (supervisor control)
```

Options:

| Flag                  | Default          | Description                              |
|-----------------------|------------------|------------------------------------------|
| ``--data``            | ``./data``       | Root data directory                      |
| ``--config``          | ``./devnet.yaml``| YAML configuration file                  |
| ``--force``           | false            | Overwrite existing data directory        |
| ``--gen-compose-file``| false            | Also generate ``docker-compose.yml``     |

## Config Patches

After genesis generation, three optional patches can be applied:

### patch_genesis

Deep-merged into ``genesis.json``.  Override any chain config field:

```yaml
patch_genesis:
  config:
    extra_fields:
      epochLength: 50
```

### patch_reth

Written as TOML into each node's data directory (``reth.toml``):

```yaml
patch_reth:
  p2p:
    max_inbound: 50
```

### patch_node_flags

Extra CLI flags appended to every ``tempo node`` invocation:

```yaml
patch_node_flags:
  - "--txpool.max-tempo-authorizations"
  - "32"
```

## Requirements

The ``tempo`` and ``tempo-xtask`` binaries must be on PATH:

```bash
# Install from source
cargo build --profile profiling --bin tempo --bin tempo-xtask

# Or download from https://docs.tempo.xyz/guide/node/installation
```
