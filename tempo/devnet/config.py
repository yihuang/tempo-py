"""Configuration loading and validation for tempo-devnet."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

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
    """Configuration for a single validator node."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int,
        moniker: str = "",
        base_port: Optional[int] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.moniker = moniker or f"node{port // 10 % 10}"
        self.base_port = base_port if base_port is not None else port

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
        return {
            "host": self.host,
            "port": self.port,
            "moniker": self.moniker,
            "base_port": self.base_port,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ValidatorConfig:
        return cls(
            host=d.get("host", "127.0.0.1"),
            port=d.get("port", d.get("base_port", 8000)),
            moniker=d.get("moniker", ""),
            base_port=d.get("base_port"),
        )


class DevnetConfig:
    """Complete devnet configuration loaded from a YAML file."""

    def __init__(self, data: dict[str, Any], source: Optional[Path] = None) -> None:
        self._source = source
        self.chain_id: int = data.get("chain_id", DEFAULT_CHAIN_ID)
        self.accounts: int = data.get("accounts", DEFAULT_ACCOUNTS)
        self.epoch_length: int = data.get("epoch_length", DEFAULT_EPOCH_LENGTH)
        self.gas_limit: int = data.get("gas_limit", DEFAULT_GAS_LIMIT)
        self.seed: Optional[int] = data.get("seed")
        self.mnemonic: str = data.get("mnemonic", DEFAULT_MNEMONIC)
        self.tempo_bin: str = data.get("tempo_bin", DEFAULT_TEMPO_BIN)
        self.tempo_xtask_bin: str = data.get("tempo_xtask_bin", DEFAULT_TEMPO_XTASK_BIN)
        self.no_dkg_in_genesis: bool = data.get("no_dkg_in_genesis", False)
        self.no_extra_tokens: bool = data.get("no_extra_tokens", False)
        self.no_pairwise_liquidity: bool = data.get("no_pairwise_liquidity", False)

        # Hardfork timestamps (default 0 = active at genesis)
        for hf in [f"t{i}_time" for i in range(9)]:
            setattr(self, hf, data.get(hf, 0))

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
        return d
