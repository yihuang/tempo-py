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

> **See also:** Ready-to-use examples in the ``examples/`` directory for
> different deployment patterns (single-network, two-network, native mode,
> custom patches, etc.).```

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
# Generate genesis + docker-compose.yaml
tempo-devnet init --data ./data --config ./devnet.yaml --gen-compose-file

# Start the cluster
docker compose -f ./data/docker-compose.yaml up -d

# Tail logs
docker compose -f ./data/docker-compose.yaml logs -f
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

In single-network mode (default), host ports use the ``base_port`` offset scheme:

| Validator | HTTP RPC       | WebSocket      | Engine API             |
|-----------|----------------|----------------|------------------------|
| node0     | ``8004:8004``  | ``8005:8005``  | ``127.0.0.1:8003:8003`` |
| node1     | ``8014:8004``  | ``8015:8005``  | ``127.0.0.1:8013:8003`` |
| node2     | ``8024:8004``  | ``8025:8005``  | ``127.0.0.1:8023:8003`` |
| node3     | ``8034:8004``  | ``8035:8005``  | ``127.0.0.1:8033:8003`` |

### Two-Network (Production Topology) Mode

When ``docker.validator_network`` is set, the devnet uses a **private validator
network + public-facing network** topology that mirrors production deployments.

#### Network Layout

::

    Validator Network (10.88.0.0/24)     Public Network (10.89.0.0/24)
    ┌──────────────────────────────┐    ┌──────────────────────────────────┐
    │ validator0 (10.88.0.10)      │    │ follower0 (10.89.0.13)           │
    │   P2P + RPC + WS (internal) │WS──┤   RPC/WS for external users     │
    │ validator1 (10.88.0.11)      │    │   --follow ws://10.88.0.10      │
    │   P2P + RPC + WS (internal) │    │                                  │
    │ validator2 (10.88.0.12)      │    │ proxy0 (10.89.0.14)             │
    │   P2P + RPC + WS (internal) │RPC─┤   p2p-proxy for external peers  │
    └──────────────────────────────┘    │   --rpc-url http://10.88.0.10   │
                                         └──────────────────────────────────┘

#### Configuration

```yaml
docker:
  image: ghcr.io/tempoxyz/tempo:latest
  # Private network for validator P2P
  validator_network:
    name: tempo-validator-net
    subnet: 10.88.0.0/24
  # Public-facing network for RPC/WS + external nodes
  public_network:
    name: tempo-public-net
    subnet: 10.89.0.0/24
  # Optional read-only follow nodes (sync from validators via WS)
  follow_nodes:
    - moniker: follower0
      port: 9000
  # Optional P2P proxy services
  p2p_proxies:
    - moniker: proxy0
      port: 7000
```

Key differences from single-network mode:

- **Validators are on the validator network only** — they are not reachable
  from the public network.  All services (P2P, RPC, WS, metrics) bind to the
  private validator-network IP only.
- **Follow nodes are dual-homed**: validator network for WS sync from
  validators (``--follow ws://10.88.0.x:8005``), public network for exposing
  RPC/WS to external users.
- **P2P proxies are dual-homed**: validator network for RPC access to
  validators (``--rpc-url http://10.88.0.x:8004``), public network for serving
  P2P to external peers.
- **Genesis peer addresses** use the private validator-network IPs, so consensus
  traffic never leaves the validator network.

#### Published Ports

Port scheme is the same as single-network mode.  Follow nodes use their own
``port`` as base:

| Service   | HTTP RPC       | WebSocket      |
|-----------|----------------|----------------|
| node0     | ``8004:8004``  | ``8005:8005``  |
| follower0 | ``9004:8004``  | ``9005:8005``  |

### External Full Nodes

Full nodes can sync from any validator's published WebSocket port:

```bash
# Single-network mode
# -or- public network IP in two-network mode
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
| ``--gen-compose-file``| false            | Also generate ``docker-compose.yaml``     |

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
