"""Backend-specific devnet generation: localnet layout + node command line.

Everything else (ports, supervisord config, run scripts, cluster management)
is backend-agnostic. Select a backend with ``backend: <name>`` in
``devnet.yaml``; binary names default to the backend name.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import DevnetConfig, ValidatorConfig
from .ports import (
    authrpc_port,
    consensus_metrics_port,
    consensus_p2p_port,
    execution_p2p_port,
    http_rpc_port,
    ws_rpc_port,
)

LOCALNET_SIGNING_KEY_SECRET = "tempo-localnet-signing-key-secret"


def write_secret_file(val_dir: Path) -> Path:
    """Write the signing-key passphrase to a ``.secret`` file in the node dir.

    Returns the path to the secret file.
    """
    secret_path = val_dir / ".secret"
    secret_path.write_text(LOCALNET_SIGNING_KEY_SECRET)
    return secret_path


def _build_common_node_args(
    *,
    tempo_bin: str,
    base_port: int,
    listen_addr: str,
    metrics_addr: str,
    rpc_addr: str,
    genesis_path: str,
    datadir: str,
    signing_key: str,
    signing_share: str,
    secret_file: str,
    enode_key: str,
    trusted_peers: list[str],
    extra_flags: list[str] | None = None,
    include_bootnodes_endpoint: bool = False,
) -> list[str]:
    """Build the argument list for ``tempo node``.

    Shared core used by native (supervisor) and both Docker topology modes.
    Callers provide the specific addresses, port base, and trusted-peers
    appropriate for their deployment mode.

    Args:
        listen_addr: IP for ``--consensus.listen-address``.
        metrics_addr: IP for ``--consensus.metrics-address``.
        rpc_addr: IP for ``--http.addr`` / ``--ws.addr``.
    """
    peers_str = ",".join(trusted_peers)

    args: list[str] = [
        tempo_bin,
        "node",
        "--consensus.signing-key",
        signing_key,
        "--consensus.secret",
        secret_file,
        "--consensus.signing-share",
        signing_share,
        "--consensus.listen-address",
        f"{listen_addr}:{consensus_p2p_port(base_port)}",
        "--consensus.metrics-address",
        f"{metrics_addr}:{consensus_metrics_port(base_port)}",
        "--chain",
        genesis_path,
        "--datadir",
        datadir,
        "--port",
        str(execution_p2p_port(base_port)),
        "--discovery.port",
        str(execution_p2p_port(base_port)),
        "--p2p-secret-key",
        enode_key,
        "--trusted-peers",
        peers_str,
        "--authrpc.port",
        str(authrpc_port(base_port)),
        "--http",
        "--http.addr",
        rpc_addr,
        "--http.port",
        str(http_rpc_port(base_port)),
        "--http.api",
        "all",
        "--ws",
        "--ws.addr",
        rpc_addr,
        "--ws.port",
        str(ws_rpc_port(base_port)),
        "--consensus.use-local-defaults",
        "--consensus.allow-private-ips",
    ]

    if include_bootnodes_endpoint:
        args.append("--tempo.bootnodes-endpoint")
        args.append("none")

    if extra_flags:
        args.extend(extra_flags)

    return args


class TempoBackend:
    """The default backend: ``tempo node`` + ``tempo-xtask generate-localnet``."""

    name = "tempo"

    def localnet_args(self, config: DevnetConfig, data_dir: Path, validators_arg: str | None = None) -> list[str]:
        return [
            config.tempo_xtask_bin,
            "generate-localnet",
            "--output",
            str(data_dir),
            "--force",
            *config.to_genesis_args(validators_arg),
        ]

    def prepare_layout(self, config: DevnetConfig, data_dir: Path, *, docker: bool = False) -> None:
        """Rename each validator's ``ip:port`` dir (named by its ``--validators``
        socket) to its moniker.  In Docker mode that socket is the container
        static IP (:meth:`DevnetConfig.docker_validator_addr`), not the host
        ``ip:port``.
        """
        rename_map: list[tuple[Path, Path]] = []
        for index, val in enumerate(config.validators):
            src_name = config.docker_validator_addr(index) if docker else val.addr_str
            src = data_dir / src_name
            dst = data_dir / val.dir_name
            if src == dst:
                continue
            if src.exists():
                if dst.exists():
                    shutil.rmtree(dst)
                rename_map.append((src, dst))

        for src, dst in rename_map:
            src.rename(dst)
            print(f"  renamed {src.name} -> {dst.name}")

    def node_args(
        self,
        config: DevnetConfig,
        val: ValidatorConfig,
        index: int,
        *,
        val_dir: Path,
        genesis_path: str,
        datadir: str,
        trusted_peers: list[str],
        extra_flags: list[str] | None = None,
    ) -> list[str]:
        write_secret_file(val_dir)
        return _build_common_node_args(
            tempo_bin=config.tempo_bin,
            base_port=val.base_port,
            listen_addr=val.p2p_host,
            metrics_addr=val.host,
            rpc_addr=val.rpc_host,
            genesis_path=genesis_path,
            datadir=datadir,
            signing_key="./signing.key",
            signing_share="./signing.share",
            secret_file="./.secret",
            enode_key="./enode.key",
            trusted_peers=trusted_peers,
            extra_flags=extra_flags,
        )


class AllegroBackend:
    """allegro: reth CLI + ``--consensus.*`` flags, ``allegro-xtask genesis``."""

    name = "allegro"

    def localnet_args(self, config: DevnetConfig, data_dir: Path, validators_arg: str | None = None) -> list[str]:
        return [
            config.tempo_xtask_bin,
            "genesis",
            "--output",
            str(data_dir),
            "--chain-id",
            str(config.chain_id),
            "--validators",
            validators_arg or config.validators_arg,
        ]

    def prepare_layout(self, config: DevnetConfig, data_dir: Path, *, docker: bool = False) -> None:
        # allegro-xtask writes only genesis.json (node identity is derived from
        # the node index); create the per-node dirs the supervisor expects.
        for val in config.validators:
            (data_dir / val.dir_name).mkdir(exist_ok=True)

    def node_args(
        self,
        config: DevnetConfig,
        val: ValidatorConfig,
        index: int,
        *,
        val_dir: Path,
        genesis_path: str,
        datadir: str,
        trusted_peers: list[str],
        extra_flags: list[str] | None = None,
    ) -> list[str]:
        # Consensus peers come from the genesis validator entries; reth p2p is
        # unused between validators (blocks flow over consensus), so discovery
        # is off and no trusted peers are passed.
        base = val.base_port
        args = [
            config.tempo_bin,
            "node",
            "--chain",
            genesis_path,
            "--datadir",
            datadir,
            "--http",
            "--http.addr",
            val.rpc_host,
            "--http.port",
            str(http_rpc_port(base)),
            "--http.api",
            "all",
            "--ws",
            "--ws.addr",
            val.rpc_host,
            "--ws.port",
            str(ws_rpc_port(base)),
            "--authrpc.port",
            str(authrpc_port(base)),
            "--port",
            str(execution_p2p_port(base)),
            "--disable-discovery",
            "--ipcdisable",
            "--consensus.node-index",
            str(index),
            "--consensus.listen-address",
            f"{val.p2p_host}:{consensus_p2p_port(base)}",
        ]
        if extra_flags:
            args.extend(extra_flags)
        return args


_BACKENDS = {"tempo": TempoBackend(), "allegro": AllegroBackend()}


def get_backend(config: DevnetConfig) -> TempoBackend | AllegroBackend:
    """The backend selected by ``config.backend`` (default tempo)."""
    try:
        return _BACKENDS[config.backend]
    except KeyError:
        raise ValueError(f"unknown backend {config.backend!r}; known: {sorted(_BACKENDS)}") from None


def generate_localnet(
    config: DevnetConfig,
    data_dir: Path,
    *,
    validators_arg: str | None = None,
    docker: bool = False,
    capture_output: bool = True,
) -> Path:
    """Generate the devnet layout (genesis + per-node dirs/keys) for ``config``.

    Runs the backend's xtask and normalizes the layout to moniker-named node
    dirs. Returns the ``genesis.json`` path; raises ``RuntimeError`` on
    failure. Pass ``capture_output=False`` to stream xtask output (CLI use).
    """
    backend = get_backend(config)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    args = backend.localnet_args(config, data_dir, validators_arg)
    print(f"Running: {' '.join(args)}")
    result = subprocess.run(args, capture_output=capture_output, text=True, check=False)
    genesis = data_dir / "genesis.json"
    if result.returncode != 0 or not genesis.exists():
        detail = f":\n{result.stderr}" if capture_output else ""
        raise RuntimeError(f"{args[0]} failed (exit {result.returncode}){detail}")
    backend.prepare_layout(config, data_dir, docker=docker)
    return genesis
