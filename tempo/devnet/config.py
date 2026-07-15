"""Configuration loading and validation for tempo-devnet."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CHAIN_ID = 1337
DEFAULT_BASE_PORT = 8000
DEFAULT_EPOCH_LENGTH = 100
DEFAULT_GAS_LIMIT = 500_000_000
DEFAULT_ACCOUNTS = 10_000
DEFAULT_MNEMONIC = "test test test test test test test test test test test junk"
DEFAULT_TEMPO_BIN = "tempo"
DEFAULT_TEMPO_XTASK_BIN = "tempo-xtask"

# Fixed internal ports — each container has its own netns, so every validator
# reuses the same numbers (offsets 0..5, mirroring ports.py).
DOCKER_CONSENSUS_P2P_PORT = 8000
DOCKER_EXECUTION_P2P_PORT = 8001
DOCKER_CONSENSUS_METRICS_PORT = 8002
DOCKER_AUTHRPC_PORT = 8003
DOCKER_HTTP_RPC_PORT = 8004
DOCKER_WS_RPC_PORT = 8005

# Consensus peers are baked into genesis as numeric ip:port (no DNS), so each
# container gets a static IP on this subnet at DOCKER_CONSENSUS_P2P_PORT.
DEFAULT_DOCKER_SUBNET = "10.88.0.0/24"
DOCKER_IP_HOST_OCTET_BASE = 10

# Default public-facing Docker network for RPC/WS exposure (used only in
# two-network topology; single-network mode uses only the validator network).
DEFAULT_DOCKER_PUBLIC_SUBNET = "10.89.0.0/24"
DEFAULT_DOCKER_PUBLIC_NETWORK = "tempo-public-net"
DOCKER_PUBLIC_IP_HOST_OCTET_BASE = 10

# Hardfork timestamp attribute names (t0_time … t8_time)
HARDFORK_ATTRS = [f"t{i}_time" for i in range(9)]


class ValidatorConfig:
    """Configuration for a single validator node.

    Attributes:
        host: Advertised IP address used in ``--trusted-peers`` and
            ``--consensus.metrics-address``.  Also used as the P2P listen
            address unless ``p2p_host`` is explicitly set.
        port: Consensus P2P port (``--consensus.listen-address``).
            Service ports are derived from this as ``base_port``.
        moniker: Node name (used as directory name and supervisor program).
        base_port: Base port for all service port calculations.
            Defaults to ``port`` (the consensus P2P port).
        p2p_host: IP to bind the P2P listener (``--consensus.listen-address``).
            Defaults to ``host``.  Set to ``0.0.0.0`` to make the validator
            reachable from external networks.
        rpc_host: IP to bind HTTP/WS JSON-RPC (``--http.addr`` / ``--ws.addr``).
            Defaults to ``0.0.0.0`` (all interfaces) so external full nodes
            can sync via ``--follow ws://<host>:<ws_port>``.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int,
        moniker: str = "",
        base_port: int | None = None,
        p2p_host: str | None = None,
        rpc_host: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.moniker = moniker or f"node{port // 10 % 10}"
        self.base_port = base_port if base_port is not None else port
        self.p2p_host = p2p_host if p2p_host is not None else host
        self.rpc_host = rpc_host if rpc_host is not None else "0.0.0.0"

    @property
    def dir_name(self) -> str:
        """Return the directory name for this validator (uses moniker)."""
        return self.moniker

    def to_validator_arg(self) -> str:
        """Convert to the ``--validators`` CLI arg format ``<ip>:<port>``."""
        return f"{self.host}:{self.port}"

    @property
    def addr_str(self) -> str:
        """Return ``ip:port`` string."""
        return f"{self.host}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "moniker": self.moniker,
            "base_port": self.base_port,
        }
        if self.p2p_host != self.host:
            d["p2p_host"] = self.p2p_host
        if self.rpc_host != "0.0.0.0":
            d["rpc_host"] = self.rpc_host
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ValidatorConfig:
        return cls(
            host=d.get("host", "127.0.0.1"),
            port=d.get("port", d.get("base_port", 8000)),
            moniker=d.get("moniker", ""),
            base_port=d.get("base_port"),
            p2p_host=d.get("p2p_host"),
            rpc_host=d.get("rpc_host"),
        )


class FollowNodeConfig:
    """Configuration for a read-only follow node in two-network mode."""

    def __init__(self, moniker: str, port: int = 9000) -> None:
        self.moniker = moniker
        self.port = port

    def to_dict(self) -> dict[str, Any]:
        return {"moniker": self.moniker, "port": self.port}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FollowNodeConfig:
        return cls(moniker=d["moniker"], port=d.get("port", 9000))


class P2PProxyConfig:
    """Configuration for a P2P proxy in two-network mode."""

    def __init__(self, moniker: str, port: int = 7000) -> None:
        self.moniker = moniker
        self.port = port

    def to_dict(self) -> dict[str, Any]:
        return {"moniker": self.moniker, "port": self.port}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> P2PProxyConfig:
        return cls(moniker=d["moniker"], port=d.get("port", 7000))


class PublicNodeConfig:
    """Configuration for a read-only public node that syncs via P2P on the public network.

    Lives on the public network only (unlike follow nodes which are dual-homed).
    Syncs blocks from the follower's WS endpoint.
    """

    def __init__(self, moniker: str, port: int = 6000) -> None:
        self.moniker = moniker
        self.port = port

    def to_dict(self) -> dict[str, Any]:
        return {"moniker": self.moniker, "port": self.port}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PublicNodeConfig:
        return cls(moniker=d["moniker"], port=d.get("port", 6000))


class DevnetConfig:
    """Complete devnet configuration loaded from a YAML file."""

    def __init__(self, data: dict[str, Any], source: Path | None = None) -> None:
        self._source = source
        self.chain_id: int = data.get("chain_id", DEFAULT_CHAIN_ID)
        self.accounts: int = data.get("accounts", DEFAULT_ACCOUNTS)
        self.epoch_length: int = data.get("epoch_length", DEFAULT_EPOCH_LENGTH)
        self.gas_limit: int = data.get("gas_limit", DEFAULT_GAS_LIMIT)
        self.seed: int | None = data.get("seed")
        self.mnemonic: str = data.get("mnemonic", DEFAULT_MNEMONIC)
        self.tempo_bin: str = data.get("tempo_bin", DEFAULT_TEMPO_BIN)
        self.tempo_xtask_bin: str = data.get("tempo_xtask_bin", DEFAULT_TEMPO_XTASK_BIN)
        self.no_dkg_in_genesis: bool = data.get("no_dkg_in_genesis", False)
        self.no_extra_tokens: bool = data.get("no_extra_tokens", False)
        self.no_pairwise_liquidity: bool = data.get("no_pairwise_liquidity", False)

        # Hardfork timestamps (default 0 = active at genesis)
        for hf in HARDFORK_ATTRS:
            setattr(self, hf, data.get(hf, 0))

        # Optional patches
        patch_genesis = data.get("patch_genesis") or {}
        if not isinstance(patch_genesis, dict):
            raise TypeError("patch_genesis must be a mapping (dict)")
        self.patch_genesis: dict[str, Any] = patch_genesis

        patch_reth = data.get("patch_reth") or {}
        if not isinstance(patch_reth, dict):
            raise TypeError("patch_reth must be a mapping (dict)")
        self.patch_reth: dict[str, Any] = patch_reth

        patch_node_flags = data.get("patch_node_flags") or []
        if not isinstance(patch_node_flags, list) or any(not isinstance(x, str) for x in patch_node_flags):
            raise TypeError("patch_node_flags must be a list of strings")
        self.patch_node_flags: list[str] = patch_node_flags

        # Docker settings + topology
        docker_raw = data.get("docker") or {}
        if not isinstance(docker_raw, dict):
            raise TypeError("docker must be a mapping (dict)")
        self.docker_image: str = docker_raw.get("image", "ghcr.io/tempoxyz/tempo:latest")
        self._init_docker_topology(docker_raw)

        # Parse validators
        raw_validators = data.get("validators", [])
        if not raw_validators:
            # Default: 4 validators on standard ports
            raw_validators = [
                {"host": "127.0.0.1", "port": 8000, "moniker": "node0"},
                {"host": "127.0.0.1", "port": 8010, "moniker": "node1"},
                {"host": "127.0.0.1", "port": 8020, "moniker": "node2"},
                {"host": "127.0.0.1", "port": 8030, "moniker": "node3"},
            ]

        self.validators = [ValidatorConfig.from_dict(v) for v in raw_validators]

    def _init_docker_topology(self, docker_raw: dict[str, Any]) -> None:
        """Configure Docker network topology from raw ``docker`` section.

        Detects two-network mode when ``validator_network`` or ``public_network``
        keys are present; otherwise falls back to single-network (legacy) mode.
        """
        self.docker_topology: str = "single"
        self.docker_network: str = docker_raw.get("network", "tempo-devnet")
        self.docker_subnet: str = docker_raw.get("subnet", DEFAULT_DOCKER_SUBNET)
        self.docker_validator_network: dict[str, str] = {}
        self.docker_public_network: dict[str, str] = {}
        self.docker_follow_nodes: list[FollowNodeConfig] = []
        self.docker_p2p_proxies: list[P2PProxyConfig] = []
        self.docker_public_nodes: list[PublicNodeConfig] = []

        if "validator_network" not in docker_raw and "public_network" not in docker_raw:
            return

        self.docker_topology = "two-network"
        self.docker_validator_network = docker_raw.get("validator_network") or {
            "name": self.docker_network,
            "subnet": self.docker_subnet,
        }
        self.docker_public_network = docker_raw.get("public_network") or {
            "name": DEFAULT_DOCKER_PUBLIC_NETWORK,
            "subnet": DEFAULT_DOCKER_PUBLIC_SUBNET,
        }
        # In two-network mode, docker_network / docker_subnet reflect the
        # validator network (used for genesis peer addressing).
        self.docker_network = self.docker_validator_network.get("name", "tempo-validator-net")
        self.docker_subnet = self.docker_validator_network.get("subnet", DEFAULT_DOCKER_SUBNET)
        self.docker_follow_nodes = [FollowNodeConfig.from_dict(f) for f in (docker_raw.get("follow_nodes") or [])]
        self.docker_p2p_proxies = [P2PProxyConfig.from_dict(p) for p in (docker_raw.get("p2p_proxies") or [])]
        self.docker_public_nodes = [PublicNodeConfig.from_dict(n) for n in (docker_raw.get("public_nodes") or [])]

    @property
    def validators_arg(self) -> str:
        """Comma-separated validator addresses for ``--validators``."""
        return ",".join(v.to_validator_arg() for v in self.validators)

    def docker_ip(self, index: int) -> str:
        """Static IP for validator ``index`` on the Docker bridge network."""
        network = ipaddress.ip_network(self.docker_subnet, strict=False)
        return str(network.network_address + DOCKER_IP_HOST_OCTET_BASE + index)

    def docker_validator_addr(self, index: int) -> str:
        """Genesis consensus address for validator ``index`` (``<static_ip>:<port>``).

        Also the directory name ``tempo-xtask`` creates (it names dirs by socket).
        """
        return f"{self.docker_ip(index)}:{DOCKER_CONSENSUS_P2P_PORT}"

    @property
    def docker_validators_arg(self) -> str:
        """``--validators`` for a Docker devnet: container static IPs, not host ports."""
        return ",".join(self.docker_validator_addr(i) for i in range(len(self.validators)))

    @property
    def docker_is_two_network(self) -> bool:
        """Whether the Docker topology uses two separate networks (validator + public)."""
        return self.docker_topology == "two-network"

    @property
    def docker_public_network_name(self) -> str:
        """Name of the public-facing Docker network (two-network mode)."""
        return self.docker_public_network.get("name", DEFAULT_DOCKER_PUBLIC_NETWORK)

    @property
    def docker_public_subnet_cidr(self) -> str:
        """CIDR of the public-facing Docker network."""
        return self.docker_public_network.get("subnet", DEFAULT_DOCKER_PUBLIC_SUBNET)

    @property
    def docker_validator_network_name(self) -> str:
        """Name of the private validator Docker network (two-network mode)."""
        if self.docker_is_two_network:
            return self.docker_validator_network.get("name", self.docker_network)
        return self.docker_network

    def docker_public_ip(self, index: int) -> str:
        """Static public IP for Docker node ``index`` on the public-facing network."""
        network = ipaddress.ip_network(self.docker_public_subnet_cidr, strict=False)
        return str(network.network_address + DOCKER_PUBLIC_IP_HOST_OCTET_BASE + index)

    @classmethod
    def load(cls, path: str | Path) -> DevnetConfig:
        """Load config from a YAML file."""
        path = Path(path)
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(data, source=path)

    def to_genesis_args(self, validators_arg: str | None = None) -> list[str]:
        """Build CLI args for ``tempo-xtask generate-localnet``.

        ``validators_arg`` overrides ``--validators`` (Docker mode passes static IPs).
        """
        args = [
            "--chain-id",
            str(self.chain_id),
            "--accounts",
            str(self.accounts),
            "--epoch-length",
            str(self.epoch_length),
            "--gas-limit",
            str(self.gas_limit),
            "--mnemonic",
            self.mnemonic,
            "--validators",
            validators_arg or self.validators_arg,
        ]
        if self.seed is not None:
            args.extend(["--seed", str(self.seed)])
        if self.no_dkg_in_genesis:
            args.append("--no-dkg-in-genesis")
        if self.no_extra_tokens:
            args.append("--no-extra-tokens")
        if self.no_pairwise_liquidity:
            args.append("--no-pairwise-liquidity")
        for hf in HARDFORK_ATTRS:
            val = getattr(self, hf)
            if val != 0:
                args.extend([f"--{hf.replace('_', '-')}", str(val)])
        return args

    def to_dict(self) -> dict[str, Any]:
        """Serialize config to a dict for YAML output."""
        d: dict[str, Any] = {
            "chain_id": self.chain_id,
            "accounts": self.accounts,
            "epoch_length": self.epoch_length,
            "gas_limit": self.gas_limit,
            "mnemonic": self.mnemonic,
            "validators": [v.to_dict() for v in self.validators],
        }
        if self.seed is not None:
            d["seed"] = self.seed
        if self.no_dkg_in_genesis:
            d["no_dkg_in_genesis"] = True
        if self.no_extra_tokens:
            d["no_extra_tokens"] = True
        if self.no_pairwise_liquidity:
            d["no_pairwise_liquidity"] = True
        for hf in HARDFORK_ATTRS:
            val = getattr(self, hf)
            if val != 0:
                d[hf] = val
        if self.patch_genesis:
            d["patch_genesis"] = self.patch_genesis
        if self.patch_reth:
            d["patch_reth"] = self.patch_reth
        if self.patch_node_flags:
            d["patch_node_flags"] = self.patch_node_flags
        if self.docker_is_two_network:
            docker_dict: dict[str, Any] = {
                "image": self.docker_image,
                "validator_network": {
                    "name": self.docker_validator_network_name,
                    "subnet": self.docker_subnet,
                },
                "public_network": {
                    "name": self.docker_public_network_name,
                    "subnet": self.docker_public_subnet_cidr,
                },
            }
            if self.docker_follow_nodes:
                docker_dict["follow_nodes"] = [f.to_dict() for f in self.docker_follow_nodes]
            if self.docker_p2p_proxies:
                docker_dict["p2p_proxies"] = [p.to_dict() for p in self.docker_p2p_proxies]
            if self.docker_public_nodes:
                docker_dict["public_nodes"] = [n.to_dict() for n in self.docker_public_nodes]
            d["docker"] = docker_dict
        else:
            d["docker"] = {
                "image": self.docker_image,
                "network": self.docker_network,
                "subnet": self.docker_subnet,
            }
        return d
