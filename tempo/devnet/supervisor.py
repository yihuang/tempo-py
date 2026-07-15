"""Supervisor configuration generation for tempo-devnet."""

from __future__ import annotations

import configparser
import json
from pathlib import Path

import jsonmerge
import tomlkit
import yaml

from .config import (
    DevnetConfig,
    FollowNodeConfig,
    P2PProxyConfig,
    ValidatorConfig,
    DOCKER_CONSENSUS_P2P_PORT,
)
from .ports import (
    authrpc_port,
    consensus_metrics_port,
    consensus_p2p_port,
    execution_p2p_port,
    http_rpc_port,
    ws_rpc_port,
)

SUPERVISOR_CONFIG_FILE = "supervisord.ini"
LOCALNET_SIGNING_KEY_SECRET = "tempo-localnet-signing-key-secret"


COMMON_PROG_OPTIONS: dict[str, str] = {
    "autostart": "true",
    "autorestart": "true",
    "redirect_stderr": "true",
    "startsecs": "3",
    "stopwaitsecs": "10",
}

# Port offset: 0=consensus-p2p, 1=execution-p2p, 2=metrics, 3=authrpc, 4=http, 5=ws
# Hard-code a generous default so nodes don't overlap
_BASE_PORT_STEP = 10


def _trusted_peers(config: DevnetConfig, data_dir: Path) -> list[str]:
    """Build trusted-peers list from enode identity files."""
    peers: list[str] = []
    for val in config.validators:
        enode_identity_file = data_dir / val.dir_name / "enode.identity"
        if not enode_identity_file.exists():
            continue
        exec_port = execution_p2p_port(val.base_port)
        peers.append(f"enode://{enode_identity_file.read_text().strip()}@{val.host}:{exec_port}")
    return peers


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


def _build_node_args(
    *,
    tempo_bin: str,
    p2p_host: str,
    rpc_host: str,
    host: str,
    base_port: int,
    genesis_path: str,
    datadir: str,
    signing_key: str,
    signing_share: str,
    secret_file: str,
    enode_key: str,
    trusted_peers: list[str],
    extra_flags: list[str] | None = None,
) -> list[str]:
    """Build the argument list for ``tempo node`` (native/supervisor mode)."""
    return _build_common_node_args(
        tempo_bin=tempo_bin,
        base_port=base_port,
        listen_addr=p2p_host,
        metrics_addr=host,
        rpc_addr=rpc_host,
        genesis_path=genesis_path,
        datadir=datadir,
        signing_key=signing_key,
        signing_share=signing_share,
        secret_file=secret_file,
        enode_key=enode_key,
        trusted_peers=trusted_peers,
        extra_flags=extra_flags,
    )


def _write_run_script_content(
    node_args: list[str],
    tag: str = "",
    *,
    include_set_eu: bool = False,
) -> str:
    """Produce a shell wrapper script that exec's a ``tempo`` command.

    The script lives in a node directory and is invoked by supervisor (native)
    or by Docker as the container entrypoint.

    Args:
        node_args: Full argument list (binary + subcommand + flags).
        tag: Optional annotation in the header comment.
        include_set_eu: Whether to add ``set -eu`` for stricter error handling.
    """
    tag_suffix = f" ({tag})" if tag else ""
    binary = _sh_quote(node_args[0])
    arg_lines = " \\\n".join("  " + _sh_quote(a) for a in node_args[1:])

    lines = [
        "#!/bin/sh",
        f"# auto-generated by tempo-devnet{tag_suffix}",
        "",
    ]
    if include_set_eu:
        lines.append("set -eu")
        lines.append("")
    lines.append('cd "$(dirname "$0")"')
    lines.append("")
    lines.append(f"exec {binary} \\")
    lines.append(arg_lines)
    lines.append("")

    return "\n".join(lines)


def write_run_script(
    val_dir: Path,
    node_args: list[str],
) -> Path:
    """Write a ``run.sh`` wrapper script that exec's the node command.

    The script uses ``exec`` so the supervisor process is replaced by the
    node process directly, keeping PID tracking clean.

    Args:
        val_dir: Node directory to write the script into.
        node_args: Full argument list for ``tempo node ...``.

    Returns:
        Path to the generated ``run.sh``.
    """
    dst = val_dir / "run.sh"
    dst.write_text(_write_run_script_content(node_args))
    dst.chmod(0o755)
    return dst


def _sh_quote(s: str) -> str:
    """Minimal shell quoting — wrap in single quotes, escaping embedded single quotes."""
    return "'" + s.replace("'", "'\\''") + "'"


def apply_genesis_patch(data_dir: Path, patch: dict) -> None:
    """Deep-merge a patch dict into ``genesis.json``.

    Args:
        data_dir: Root directory containing ``genesis.json``.
        patch: Dict of genesis fields to override (deep-merged).
    """
    if not patch:
        return

    genesis_path = data_dir / "genesis.json"
    if not genesis_path.exists():
        return

    with open(genesis_path) as f:
        genesis = json.load(f)

    genesis = jsonmerge.merge(genesis, patch)

    with open(genesis_path, "w") as f:
        json.dump(genesis, f, indent=2)

    print(f"  patched genesis.json with {len(patch)} top-level key(s)")


def write_reth_config(val_dir: Path, patch: dict) -> None:
    """Write a ``reth.toml`` config file into a node's data directory.

    Uses ``tomlkit`` for proper TOML serialization.  Only non-empty patches
    produce output.

    Args:
        val_dir: Node data directory.
        patch: Dict of reth config options.
    """
    if not patch:
        return

    reth_path = val_dir / "reth.toml"
    with open(reth_path, "w") as f:
        tomlkit.dump(patch, f)
    print(f"  wrote reth.toml to {val_dir.name}/")


def generate_supervisor_config(
    config: DevnetConfig,
    data_dir: Path,
    *,
    force: bool = False,
) -> Path:
    """Generate a supervisor configuration file for all validator nodes.

    Also writes:
    - A ``.secret`` passphrase file inside each node directory.
    - A ``run.sh`` wrapper script that exec's the node command.
    - ``reth.toml`` per node if ``config.patch_reth`` is set.
    - Patches ``genesis.json`` if ``config.patch_genesis`` is set.

    The supervisor command is an absolute path to ``run.sh``.

    Args:
        config: The devnet configuration.
        data_dir: Root directory containing genesis.json and validator dirs.
        force: Overwrite existing supervisor config.

    Returns:
        Path to the generated ``supervisord.ini`` file.
    """
    data_dir = data_dir.resolve()

    # Apply genesis patch first
    apply_genesis_patch(data_dir, config.patch_genesis)

    # Copy patched genesis into each validator directory for isolation
    genesis_src = data_dir / "genesis.json"
    if genesis_src.exists():
        for val in config.validators:
            (data_dir / val.dir_name / "genesis.json").write_bytes(genesis_src.read_bytes())

    dst = data_dir / SUPERVISOR_CONFIG_FILE
    if dst.exists() and not force:
        return dst

    tempo_bin = config.tempo_bin

    ini = configparser.RawConfigParser()
    ini.add_section("supervisord")
    ini["supervisord"] = {
        "pidfile": f"{data_dir}/supervisord.pid",
        "nodaemon": "true",
        "logfile": f"{data_dir}/supervisord.log",
        "logfile_maxbytes": "0",
        "strip_ansi": "true",
    }

    ini.add_section("rpcinterface:supervisor")
    ini["rpcinterface:supervisor"] = {
        "supervisor.rpcinterface_factory": "supervisor.rpcinterface:make_main_rpcinterface",
    }

    sock = data_dir / "supervisor.sock"
    ini.add_section("unix_http_server")
    ini["unix_http_server"] = {"file": str(sock)}

    ini.add_section("supervisorctl")
    ini["supervisorctl"] = {"serverurl": f"unix://{sock}"}

    # Derive trusted peers from enode identity files
    peers = _trusted_peers(config, data_dir)

    for val in config.validators:
        val_dir = data_dir / val.dir_name
        prgname = f"program:{val.dir_name}"
        ini.add_section(prgname)

        # Write the secret file for --consensus.secret
        write_secret_file(val_dir)

        # All paths are relative — the wrapper script cds to the node dir first
        node_args = _build_node_args(
            tempo_bin=tempo_bin,
            p2p_host=val.p2p_host,
            rpc_host=val.rpc_host,
            host=val.host,
            base_port=val.base_port,
            genesis_path="./genesis.json",
            datadir=".",
            signing_key="./signing.key",
            signing_share="./signing.share",
            secret_file="./.secret",
            enode_key="./enode.key",
            trusted_peers=peers,
            extra_flags=config.patch_node_flags or None,
        )

        # Write the wrapper script
        write_run_script(val_dir, node_args)

        # Write per-node reth config
        write_reth_config(val_dir, config.patch_reth)

        # Supervisor runs the wrapper script via absolute path.
        # Using an absolute path avoids issues with supervisor's execve
        # (no shell involved) and the working directory.
        ini[prgname] = dict(
            COMMON_PROG_OPTIONS,
            directory=str(val_dir),
            command=str(val_dir / "run.sh"),
            stdout_logfile=f"{val_dir}/node.log",
        )

    with open(dst, "w") as fp:
        ini.write(fp)

    return dst


DOCKER_CONFIG_FILE = "docker-compose.yaml"
CONTAINER_DATA_DIR = "/data"

# Fixed internal ports are derived from DOCKER_CONSENSUS_P2P_PORT via the
# port functions in ports.py, so the genesis generator (which bakes static
# IP:port into genesis) and the compose generator always agree.


_DOCKER_EXECUTION_P2P = execution_p2p_port(DOCKER_CONSENSUS_P2P_PORT)


def _docker_trusted_peers(
    config: DevnetConfig,
    data_dir: Path,
    *,
    exclude_moniker: str = "",
) -> list[str]:
    """Build trusted-peers list for Docker mode using service names.

    Each peer is ``enode://<id>@<moniker>:<fixed_execution_p2p>`` so Docker's
    internal DNS resolves them across containers.
    """
    peers: list[str] = []
    for other in config.validators:
        if exclude_moniker and other.moniker == exclude_moniker:
            continue
        enode_id_file = data_dir / other.dir_name / "enode.identity"
        if not enode_id_file.exists():
            continue
        peers.append(f"enode://{enode_id_file.read_text().strip()}@{other.moniker}:{_DOCKER_EXECUTION_P2P}")
    return peers


def _docker_node_command(
    config: DevnetConfig,
    val: ValidatorConfig,
    data_dir: Path,
) -> list[str]:
    """Build the ``tempo node`` argument list for a Docker container (single-network).

    All containers bind to ``0.0.0.0`` since each has its own network namespace.
    Trusted-peers use Docker service names.
    """
    return _build_common_node_args(
        tempo_bin=config.tempo_bin,
        base_port=DOCKER_CONSENSUS_P2P_PORT,
        listen_addr="0.0.0.0",
        metrics_addr="0.0.0.0",
        rpc_addr="0.0.0.0",
        genesis_path="./genesis.json",
        datadir=".",
        signing_key="./signing.key",
        signing_share="./signing.share",
        secret_file="./.secret",
        enode_key="./enode.key",
        trusted_peers=_docker_trusted_peers(config, data_dir, exclude_moniker=val.moniker),
        extra_flags=config.patch_node_flags or None,
        include_bootnodes_endpoint=True,
    )


def _docker_node_two_network_command(
    config: DevnetConfig,
    val: ValidatorConfig,
    data_dir: Path,
    index: int,
) -> list[str]:
    """Build the ``tempo node`` argument list for a validator in two-network mode.

    Validators are **only on the validator network** — they are not reachable
    from the public network.  All services (P2P, RPC, metrics) bind to the
    private validator-network IP.  External access is mediated by follow nodes
    and P2P proxies that bridge the two networks.

    Trusted-peers use Docker service names (resolved on the validator network).
    ``--http.addr`` binds to the validator-network IP so follow nodes on the
    same network can sync via WS.
    """
    val_ip = config.docker_ip(index)
    return _build_common_node_args(
        tempo_bin=config.tempo_bin,
        base_port=DOCKER_CONSENSUS_P2P_PORT,
        listen_addr=val_ip,
        metrics_addr=val_ip,
        rpc_addr=val_ip,
        genesis_path="./genesis.json",
        datadir=".",
        signing_key="./signing.key",
        signing_share="./signing.share",
        secret_file="./.secret",
        enode_key="./enode.key",
        trusted_peers=_docker_trusted_peers(config, data_dir, exclude_moniker=val.moniker),
        extra_flags=config.patch_node_flags or None,
        include_bootnodes_endpoint=True,
    )


def _docker_follow_node_command(
    config: DevnetConfig,
    follow: FollowNodeConfig,
    data_dir: Path,
    *,
    follow_idx: int = 0,
) -> list[str]:
    """Build the ``tempo node --follow`` argument list for a follow node.

    Follow nodes are dual-homed:
    - Validator network: sync from validators via WS (``10.88.0.x:8005``)
    - Public network: expose HTTP/WS RPC externally

    They do not have a BLS signing share and do not participate in consensus.
    """
    # Follow from validator0 on the validator network (private IP).
    val_ws_ip = config.docker_ip(0)
    upstream_ws = f"ws://{val_ws_ip}:{ws_rpc_port(DOCKER_CONSENSUS_P2P_PORT)}"

    args: list[str] = [
        config.tempo_bin,
        "node",
        "--follow",
        upstream_ws,
        "--chain",
        "./genesis.json",
        "--datadir",
        ".",
        "--consensus.signing-key",
        "./signing.key",
        "--http",
        "--http.addr",
        "0.0.0.0",
        "--http.port",
        str(http_rpc_port(DOCKER_CONSENSUS_P2P_PORT)),
        "--http.api",
        "all",
        "--ws",
        "--ws.addr",
        "0.0.0.0",
        "--ws.port",
        str(ws_rpc_port(DOCKER_CONSENSUS_P2P_PORT)),
        "--consensus.use-local-defaults",
    ]

    if config.patch_node_flags:
        args.extend(config.patch_node_flags)

    return args


def _docker_p2p_proxy_command(
    config: DevnetConfig,
    proxy: P2PProxyConfig,
    data_dir: Path,
) -> list[str]:
    """Build the ``tempo p2p-proxy`` argument list for a P2P proxy service.

    P2P proxies are dual-homed:
    - Validator network: sync from validators via RPC (``10.88.0.x:8004``)
    - Public network: expose block data over P2P to external peers
    """
    # Pick the first validator as the upstream RPC source on the validator network.
    val_rpc_ip = config.docker_ip(0)
    upstream_rpc = f"http://{val_rpc_ip}:{http_rpc_port(DOCKER_CONSENSUS_P2P_PORT)}"

    args: list[str] = [
        config.tempo_bin,
        "p2p-proxy",
        "--rpc-url",
        upstream_rpc,
        "--chain",
        "./genesis.json",
        "--p2p-secret-key",
        "./enode.key",
        "--port",
        str(execution_p2p_port(DOCKER_CONSENSUS_P2P_PORT)),
    ]

    if config.patch_node_flags:
        args.extend(config.patch_node_flags)

    return args


def write_docker_run_script(val_dir: Path, node_args: list[str]) -> Path:
    """Write a ``docker-run.sh`` wrapper script using container-relative paths.

    The script lives inside the node directory and is invoked inside the
    Docker container where ``/data`` is mounted.
    """
    dst = val_dir / "docker-run.sh"
    dst.write_text(_write_run_script_content(node_args, tag="docker", include_set_eu=True))
    dst.chmod(0o755)
    return dst


def generate_docker_compose(
    config: DevnetConfig,
    data_dir: Path,
    *,
    force: bool = False,
) -> Path:
    """Generate a ``docker-compose.yaml`` to run the devnet in Docker.

    Supports two deployment topologies:

    **Single-network mode** (default, backward-compatible):
    All containers on one bridge network.  P2P traffic stays inside the Docker
    network; RPC/WS ports are published to the host.

    **Two-network mode** (``config.docker_is_two_network``):
    Emulates the production deployment topology:
    - Validators are on the private validator network only (not reachable from the public network).
      All services (P2P, RPC/WS, metrics) bind to the validator-network IP.
    - Follow nodes (read-only) are dual-homed: validator network for WS sync, public network for RPC/WS.
    - P2P proxies are dual-homed: validator network for RPC access, public network for P2P exposure.

    A ``docker-run.sh`` wrapper script is written into each validator
    directory; the docker-compose command calls it via the mounted volume.

    Requires the tempo Docker image specified in ``config.docker_image``.
    """
    # Absolute path so the volume source is a bind mount, not a named volume.
    data_dir = data_dir.resolve()

    dst = data_dir / DOCKER_CONFIG_FILE
    if dst.exists() and not force:
        return dst

    services: dict[str, dict]
    if config.docker_is_two_network:
        services = _generate_two_network_compose(config, data_dir)
    else:
        services = _generate_single_network_compose(config, data_dir)

    # Build network definitions
    networks: dict[str, dict] = {}
    if config.docker_is_two_network:
        networks[config.docker_validator_network_name] = {
            "driver": "bridge",
            "ipam": {"config": [{"subnet": config.docker_subnet}]},
        }
        networks[config.docker_public_network_name] = {
            "driver": "bridge",
            "ipam": {"config": [{"subnet": config.docker_public_subnet_cidr}]},
        }
    else:
        networks[config.docker_network] = {
            "driver": "bridge",
            "ipam": {"config": [{"subnet": config.docker_subnet}]},
        }

    compose: dict = {
        "services": services,
        "networks": networks,
    }

    with open(dst, "w") as f:
        yaml.dump(compose, f, default_flow_style=False, sort_keys=False)

    total = len(config.validators) + len(config.docker_follow_nodes) + len(config.docker_p2p_proxies)
    print(f"  wrote docker-compose.yaml ({total} services, {config.docker_topology} network mode)")
    return dst


def _generate_single_network_compose(config: DevnetConfig, data_dir: Path) -> dict[str, dict]:
    """Generate docker-compose services for single-network (legacy) mode.

    All containers on one bridge network.  This is the original behavior.
    """
    services: dict[str, dict] = {}

    for index, val in enumerate(config.validators):
        val_dir = data_dir / val.dir_name
        write_docker_run_script(val_dir, _docker_node_command(config, val, data_dir))

        # Publish HTTP/WS RPC to the host; authrpc (engine API) stays internal
        # (reth binds it to localhost, so a published port never forwards).
        container_http = http_rpc_port(DOCKER_CONSENSUS_P2P_PORT)
        container_ws = ws_rpc_port(DOCKER_CONSENSUS_P2P_PORT)
        published_ports = [
            f"{http_rpc_port(val.base_port)}:{container_http}",
            f"{ws_rpc_port(val.base_port)}:{container_ws}",
        ]

        services[val.moniker] = {
            "image": config.docker_image,
            "entrypoint": ["/bin/sh"],
            "command": f"{CONTAINER_DATA_DIR}/docker-run.sh",
            "volumes": [f"{data_dir / val.dir_name}:{CONTAINER_DATA_DIR}"],
            "ports": published_ports,
            "networks": {config.docker_network: {"ipv4_address": config.docker_ip(index)}},
        }

    return services


def _prepare_follow_node_dir(
    config: DevnetConfig,
    data_dir: Path,
    moniker: str,
    follow: FollowNodeConfig,
) -> None:
    """Set up a follow node directory with genesis and key files."""
    f_dir = data_dir / moniker
    f_dir.mkdir(parents=True, exist_ok=True)

    genesis_src = data_dir / "genesis.json"
    if genesis_src.exists():
        (f_dir / "genesis.json").write_bytes(genesis_src.read_bytes())

    if not (f_dir / "signing.key").exists():
        # 32-byte ed25519 private key as hex (valid format, not used for consensus)
        (f_dir / "signing.key").write_text("0000000000000000000000000000000000000000000000000000000000000001")
    if not (f_dir / "enode.key").exists():
        (f_dir / "enode.key").write_text("0000000000000000000000000000000000000000000000000000000000000003")

    write_docker_run_script(f_dir, _docker_follow_node_command(config, follow, data_dir))


def _prepare_proxy_dir(
    config: DevnetConfig,
    data_dir: Path,
    moniker: str,
    proxy: P2PProxyConfig,
) -> None:
    """Set up a P2P proxy directory with enode key and run script."""
    p_dir = data_dir / moniker
    p_dir.mkdir(parents=True, exist_ok=True)

    genesis_src = data_dir / "genesis.json"
    if genesis_src.exists():
        (p_dir / "genesis.json").write_bytes(genesis_src.read_bytes())

    if not (p_dir / "enode.key").exists():
        # 32-byte ed25519 private key as hex (valid format)
        (p_dir / "enode.key").write_text("0000000000000000000000000000000000000000000000000000000000000002")

    write_docker_run_script(p_dir, _docker_p2p_proxy_command(config, proxy, data_dir))


def _generate_two_network_compose(config: DevnetConfig, data_dir: Path) -> dict[str, dict]:
    """Generate docker-compose services for two-network (production topology) mode.

    Network topology (emulates production):

        Validator Network (private)           Public Network
        ┌──────────────────────────┐         ┌──────────────────────────┐
        │ validator0               │         │ follower0  (RPC/WS)      │
        │   P2P, RPC, WS          │WS────────  --follow ws://val0     │
        │ validator1               │         │ proxy0     (P2P)        │
        │   P2P, RPC, WS          │RPC───────  --rpc-url http://val0  │
        │ validator2               │         └──────────────────────────┘
        └──────────────────────────┘

    - Validators: validator network only (isolated from public network)
    - Follow nodes: dual-homed (validator net for WS sync, public net for RPC)
    - P2P proxies: dual-homed (validator net for RPC access, public net for P2P)

    Published ports from validator containers provide devnet convenience access
    to RPC/WS from the host, but validators are NOT reachable from the public
    Docker network.
    """
    services: dict[str, dict] = {}

    # Track the next available public IP offset for non-validator services
    f_start = len(config.validators)

    # --- Validators: validator network only ---
    for index, val in enumerate(config.validators):
        val_dir = data_dir / val.dir_name
        write_docker_run_script(val_dir, _docker_node_two_network_command(config, val, data_dir, index))

        container_http = http_rpc_port(DOCKER_CONSENSUS_P2P_PORT)
        container_ws = ws_rpc_port(DOCKER_CONSENSUS_P2P_PORT)
        published_ports = [
            f"{http_rpc_port(val.base_port)}:{container_http}",
            f"{ws_rpc_port(val.base_port)}:{container_ws}",
        ]

        services[val.moniker] = {
            "image": config.docker_image,
            "entrypoint": ["/bin/sh"],
            "command": f"{CONTAINER_DATA_DIR}/docker-run.sh",
            "volumes": [f"{data_dir / val.dir_name}:{CONTAINER_DATA_DIR}"],
            "ports": published_ports,
            # Only on validator network — NOT on public network
            "networks": {
                config.docker_validator_network_name: {
                    "ipv4_address": config.docker_ip(index),
                },
            },
        }

    # --- Follow nodes: dual-homed (validator net for WS, public net for RPC) ---
    for f_idx, follow in enumerate(config.docker_follow_nodes):
        f_moniker: str = follow.moniker
        f_port: int = follow.port

        _prepare_follow_node_dir(config, data_dir, f_moniker, follow)

        # Follow node on validator network for WS sync (last validator IP + 1 + f_idx)
        # and on public network for RPC exposure.
        f_val_ip = config.docker_ip(f_start + f_idx)
        f_pub_ip = config.docker_public_ip(f_start + f_idx)

        services[f_moniker] = {
            "image": config.docker_image,
            "entrypoint": ["/bin/sh"],
            "command": f"{CONTAINER_DATA_DIR}/docker-run.sh",
            "volumes": [f"{data_dir / f_moniker}:{CONTAINER_DATA_DIR}"],
            "ports": [
                f"{http_rpc_port(f_port)}:{http_rpc_port(DOCKER_CONSENSUS_P2P_PORT)}",
                f"{ws_rpc_port(f_port)}:{ws_rpc_port(DOCKER_CONSENSUS_P2P_PORT)}",
            ],
            "networks": {
                config.docker_validator_network_name: {
                    "ipv4_address": f_val_ip,
                },
                config.docker_public_network_name: {
                    "ipv4_address": f_pub_ip,
                },
            },
        }

    # --- P2P proxies: dual-homed (validator net for RPC, public net for P2P) ---
    p_start = f_start + len(config.docker_follow_nodes)
    for p_idx, proxy in enumerate(config.docker_p2p_proxies):
        p_moniker: str = proxy.moniker
        p_port: int = proxy.port

        _prepare_proxy_dir(config, data_dir, p_moniker, proxy)

        p_val_ip = config.docker_ip(p_start + p_idx)
        p_pub_ip = config.docker_public_ip(p_start + p_idx)

        services[p_moniker] = {
            "image": config.docker_image,
            "entrypoint": ["/bin/sh"],
            "command": f"{CONTAINER_DATA_DIR}/docker-run.sh",
            "volumes": [f"{data_dir / p_moniker}:{CONTAINER_DATA_DIR}"],
            "ports": [
                f"{p_port}:{execution_p2p_port(DOCKER_CONSENSUS_P2P_PORT)}",
            ],
            "networks": {
                config.docker_validator_network_name: {
                    "ipv4_address": p_val_ip,
                },
                config.docker_public_network_name: {
                    "ipv4_address": p_pub_ip,
                },
            },
        }

    return services
