"""Configuration loading and validation for tempo-devnet."""

from __future__ import annotations

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
        for hf in [f"t{i}_time" for i in range(9)]:
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

        # Docker settings
        docker_raw = data.get("docker") or {}
        if not isinstance(docker_raw, dict):
            raise TypeError("docker must be a mapping (dict)")
        self.docker_image: str = docker_raw.get("image", "ghcr.io/tempoxyz/tempo:latest")
        self.docker_network: str = docker_raw.get("network", "tempo-devnet")
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

    @property
    def validators_arg(self) -> str:
        """Comma-separated validator addresses for ``--validators``."""
        return ",".join(v.to_validator_arg() for v in self.validators)

    @classmethod
    def load(cls, path: str | Path) -> DevnetConfig:
        """Load config from a YAML file."""
        path = Path(path)
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(data, source=path)

    def to_genesis_args(self) -> list[str]:
        """Build CLI args for ``tempo-xtask generate-localnet``."""
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
            self.validators_arg,
        ]
        if self.seed is not None:
            args.extend(["--seed", str(self.seed)])
        if self.no_dkg_in_genesis:
            args.append("--no-dkg-in-genesis")
        if self.no_extra_tokens:
            args.append("--no-extra-tokens")
        if self.no_pairwise_liquidity:
            args.append("--no-pairwise-liquidity")
        for hf in [f"t{i}_time" for i in range(9)]:
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
        for hf in [f"t{i}_time" for i in range(9)]:
            val = getattr(self, hf)
            if val != 0:
                d[hf] = val
        if self.patch_genesis:
            d["patch_genesis"] = self.patch_genesis
        if self.patch_reth:
            d["patch_reth"] = self.patch_reth
        if self.patch_node_flags:
            d["patch_node_flags"] = self.patch_node_flags
        d["docker"] = {
            "image": self.docker_image,
            "network": self.docker_network,
        }
        return d
