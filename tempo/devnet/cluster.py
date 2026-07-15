"""ClusterCLI — interact with a running tempo-devnet cluster.

Provides access to the supervisor RPC interface, node status lookups,
and convenience methods for querying a running cluster. Running supervisord
itself is left to the caller (the CLI ``start`` command, or a test harness
managing its own child process).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from supervisor import xmlrpc
from supervisor.compat import xmlrpclib

from .config import DevnetConfig
from .ports import http_rpc_port, ws_rpc_port


def _find_rpc_endpoint(config: DevnetConfig, moniker: str) -> tuple[str, int]:
    """Resolve a moniker (validator, follow, or public node) to its host and base_port.

    Raises ``KeyError`` if unknown.
    """
    for v in config.validators:
        if v.moniker == moniker:
            return v.host, v.base_port
    for n in (*config.docker_follow_nodes, *config.docker_public_nodes):
        if n.moniker == moniker:
            return "127.0.0.1", n.port
    raise KeyError(f"node {moniker!r} not found")


class ClusterCLI:
    """API to interact with a running tempo-devnet cluster.

    Args:
        data_dir: Root directory of the devnet (contains ``genesis.json``,
            validator subdirectories, and ``supervisord.ini``).
        config: Optional ``DevnetConfig`` instance. If not provided, loaded
            from ``data_dir/devnet.yaml`` or auto-detected.
    """

    def __init__(
        self,
        data_dir: str | Path,
        config: DevnetConfig | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self._config = config
        self._supervisor_proxy: object | None = None

    @property
    def config(self) -> DevnetConfig:
        if self._config is None:
            config_path = self.data_dir / "devnet.yaml"
            if config_path.exists():
                self._config = DevnetConfig.load(config_path)
            else:
                # Use defaults with validators from directory structure
                validators = sorted(p.name for p in self.data_dir.iterdir() if p.is_dir() and p.name.startswith("node"))
                raw = {
                    "validators": [
                        {"host": "127.0.0.1", "port": 8000 + i * 10, "moniker": name}
                        for i, name in enumerate(validators)
                    ]
                }
                self._config = DevnetConfig(raw)
        return self._config

    # ------------------------------------------------------------------
    # Supervisor control
    # ------------------------------------------------------------------

    @property
    def supervisor(self) -> object:
        """The supervisor XML-RPC proxy (lazy-init)."""
        if self._supervisor_proxy is None:
            self._supervisor_proxy = xmlrpclib.ServerProxy(
                "http://127.0.0.1",
                transport=xmlrpc.SupervisorTransport(
                    serverurl=f"unix://{self.data_dir / 'supervisor.sock'}",
                ),
            )
        return self._supervisor_proxy.supervisor

    def status(self) -> list[dict[str, Any]]:
        """Get status of all supervised processes.

        Returns:
            List of dicts with keys: ``name``, ``group``, ``state``,
            ``statename``, ``pid``, ``uptime_seconds``, ``description``.
        """
        try:
            raw = self.supervisor.getAllProcessInfo()
        except Exception:
            # A request that failed mid-flight (e.g. supervisord still booting)
            # leaves the cached transport unusable ("Request-sent"); drop it so
            # the caller's retry reconnects cleanly.
            self._supervisor_proxy = None
            raise
        return list(raw)

    def start_node(self, moniker: str) -> bool:
        """Start a specific validator node by its moniker."""
        return self.supervisor.startProcess(moniker)

    def stop_node(self, moniker: str) -> bool:
        """Stop a specific validator node."""
        return self.supervisor.stopProcess(moniker)

    def start_all(self) -> bool:
        """Start all validator nodes."""
        for info in self.status():
            if info["statename"] == "STOPPED":
                self.supervisor.startProcess(info["name"])
        return True

    def stop_all(self) -> bool:
        """Stop all validator nodes."""
        for info in self.status():
            self.supervisor.stopProcess(info["name"])
        return True

    def restart_node(self, moniker: str) -> bool:
        """Restart a specific validator node."""
        return self.supervisor.stopProcess(moniker) and self.supervisor.startProcess(moniker)

    # ------------------------------------------------------------------
    # Node info
    # ------------------------------------------------------------------

    def node_rpc_url(self, moniker: str) -> str:
        """Get the HTTP RPC URL for a validator node by moniker."""
        host, base_port = _find_rpc_endpoint(self.config, moniker)
        return f"http://{host}:{http_rpc_port(base_port)}"

    def node_ws_url(self, moniker: str) -> str:
        """Get the WebSocket RPC URL for a validator node by moniker."""
        host, base_port = _find_rpc_endpoint(self.config, moniker)
        return f"ws://{host}:{ws_rpc_port(base_port)}"

    def node_dirs(self) -> list[Path]:
        """List all validator data directories (named by moniker)."""
        return sorted(p for p in self.data_dir.iterdir() if p.is_dir() and p.name.startswith("node"))

    def summary(self) -> str:
        """Print a human-readable cluster summary."""
        lines = [f"Devnet: {self.data_dir}"]
        lines.append(f"  Chain ID: {self.config.chain_id}")
        lines.append(f"  Genesis:  {self.data_dir / 'genesis.json'}")
        lines.append("")

        proc_info = self.status()
        by_name = {p["name"]: p for p in proc_info}

        lines.append(f"{'Moniker':<16} {'Status':<10} {'PID':<8} {'RPC URL'}")
        lines.append("-" * 72)

        for v in self.config.validators:
            info = by_name.get(v.moniker, {})
            state = info.get("statename", "N/A")
            pid = str(info.get("pid", "-"))
            rpc = self.node_rpc_url(v.moniker)
            lines.append(f"{v.moniker:<16} {state:<10} {pid:<8} {rpc}")

        return "\n".join(lines)
