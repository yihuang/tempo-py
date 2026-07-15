# Devnet Configuration Examples

This directory contains example `devnet.yaml` configurations for three
deployment patterns. Each file is self-documenting and ready to use.

## Quick Reference

| File | Mode | Validators | Docker | Periphery | Best For |
|------|------|------------|--------|-----------|----------|
| `native.yaml` | native (supervisord) | 3 | — | — | Local dev, process debugging, profiling |
| `single-network.yaml` | single bridge | 4 | 1 network | — | CI, smoke tests, fastest startup |
| `two-network.yaml` | two networks | 4 | 2 networks | follower + proxy + public node | Production topology simulation |

## Architecture

### Native Mode (supervisord)

```
                          Host
  ┌─────────────────────────────────────────────────┐
  │  supervisord manages all processes              │
  │                                                  │
  │  node0 (pid 1234)      node1 (pid 1235)         │
  │    P2P :8000              P2P :8010              │
  │    RPC :8004              RPC :8014              │
  │                                                  │
  │  Logs: data/node0/node.log                       │
  └─────────────────────────────────────────────────┘
```

No Docker — each validator runs as a native OS process managed by
supervisord. Good for development when you want direct log access,
process-level debugging, or resource profiling.

**Choose when:** you need to profile CPU/memory per validator, debug
process startup, or prefer simple file-based log management.

### Single-Network Mode

```
                          Host
  ┌─────────────────────────────────────────────────┐
  │  Docker Bridge (10.88.0.0/24)                   │
  │  ┌──────────┐  ┌──────────┐                     │
  │  │ node0    │  │ node1    │                     │
  │  │ P2P RPC  │  │ P2P RPC  │                     │
  │  └────:8004─┘  └────:8014─┘                     │
  │       │            │                             │
  └───────┼────────────┼─────────────────────────────┘
          │:8004       │:8014
          ▼            ▼
    curl localhost   curl localhost
```

All nodes on one bridge. Simple, but no network-level isolation.

**Choose when:** you want the simplest Docker setup and don't need to test
network topology concerns.

### Two-Network Mode

```
  Validator Network (10.88.0.0/24)      Public Network (10.89.0.0/24)
  ┌──────────────────────────────┐    ┌──────────────────────────────────┐
  │ node0 (10.88.0.10)           │    │ follower0 (10.89.0.13)           │
  │   P2P + RPC + WS (internal) │WS──┤   RPC/WS for external users     │
  │ node1 (10.88.0.11)           │    │   --follow ws://10.88.0.10      │
  │ node2 (10.88.0.12)           │    │                                  │
  │                              │    │ proxy0 (10.89.0.14)             │
  │ validator network only             │RPC─┤   p2p-proxy for external peers  │
  │ no public network access            │    └──────────────────────────────────┘
  └──────────────────────────────┘
```

Validators are **isolated** on a private network. External services
(follow nodes, P2P proxies) bridge to a public-facing network.

**Choose when:** you want to test production deployment topology, network
isolation, or the follower/proxy/gateway pattern.

## Usage

### Native Dev

```bash
# Requires tempo and tempo-xtask on PATH
tempo-devnet serve --data ./data --config examples/native.yaml
```

### Docker Compose (single-network or two-network)

```bash
# Init genesis, keys, and docker-compose.yaml
tempo-devnet init --data ./data --config examples/single-network.yaml --gen-compose-file

# Start all containers
docker compose -f ./data/docker-compose.yaml up -d

# Check status
docker compose -f ./data/docker-compose.yaml ps

# Tail all logs
docker compose -f ./data/docker-compose.yaml logs -f

# Follow a single node's logs
docker compose -f ./data/docker-compose.yaml logs -f node0

# Check block production (native or Docker)
curl http://127.0.0.1:8004 -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'

# Stop everything
docker compose -f ./data/docker-compose.yaml down

# Reset and restart from scratch
tempo-devnet init --data ./data --config examples/single-network.yaml --force --gen-compose-file
docker compose -f ./data/docker-compose.yaml up -d
```

## Prerequisites

All Docker Compose examples require:

```bash
# 1. Build tempo-xtask (host binary for genesis generation)
cd ../tempo
cargo build --profile profiling --bin tempo-xtask
export PATH=$PWD/target/profiling:$PATH

# 2. Build the Docker image
docker buildx bake tempo --load
# or: docker build -t tempo:local --target tempo .

# 3. (Optional) Use a locally built image by overriding in config:
#    image: tempo:local
```

Native mode (`native.yaml`) additionally requires:

```bash
cargo build --profile profiling --bin tempo
export PATH=$PWD/target/profiling:$PATH
```

## Customization

The examples cover the three common topologies. For advanced use cases,
you can mix and match these settings:

- **`patch_genesis`** — Deep-merge custom fields into `genesis.json`
  (e.g., override `extra_fields`, chain parameters).
- **`patch_reth`** — Write a per-node `reth.toml` with custom Reth settings
  (e.g., P2P limits, database size).
- **`patch_node_flags`** — Append extra CLI flags to every `tempo node`
  invocation (e.g., `--txpool.*`, `--builder.*`).
- **`t0_time` … `t8_time`** — Set hardfork activation timestamps
  (default 0 = active at genesis).
