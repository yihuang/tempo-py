"""tempo-devnet CLI — manage multi-node Tempo testnets.

Usage examples::

    # Quick start (init + start in one shot)
    tempo-devnet serve --data ./data --config ./devnet.yaml

    # Step by step
    tempo-devnet init --data ./data --config ./devnet.yaml
    tempo-devnet start --data ./data

    # Supervisor control
    tempo-devnet supervisorctl status
    tempo-devnet supervisorctl tail node0

    # Cluster info
    tempo-devnet status --data ./data
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import fire
import yaml
from supervisor.supervisorctl import main as supervisorctl_main

from .cluster import ClusterCLI
from .config import DevnetConfig
from .supervisor import SUPERVISOR_CONFIG_FILE, generate_supervisor_config


def _cmd_exists(name: str) -> bool:
    """Check if a command exists on PATH."""
    path = os.environ.get("PATH", "")
    for dir_path in path.split(os.pathsep):
        exe = os.path.join(dir_path, name)
        if os.path.isfile(exe) and os.access(exe, os.X_OK):
            return True
    return False


def _ensure_tempo_bins(config: DevnetConfig) -> None:
    """Verify that the required tempo binaries are available."""
    missing = []
    if not _cmd_exists(config.tempo_bin):
        missing.append(config.tempo_bin)
    if not _cmd_exists(config.tempo_xtask_bin):
        missing.append(config.tempo_xtask_bin)
    if missing:
        print(
            f"Error: required binaries not found on PATH: {', '.join(missing)}",
            file=sys.stderr,
        )
        print(
            "Make sure tempo and tempo-xtask are installed and on your PATH.",
            file=sys.stderr,
        )
        sys.exit(1)


def _rename_validator_dirs(data_dir: Path, config: DevnetConfig) -> None:
    """Rename validator directories from ``ip:port`` (created by ``generate-localnet``)
    to moniker-based names.
    """
    rename_map: list[tuple[Path, Path]] = []
    for val in config.validators:
        src = data_dir / val.addr_str
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


def init(
    data: str = "./data",
    config: str = "./devnet.yaml",
    force: bool = False,
    **kwargs: str | int | bool | None,
) -> None:
    """Initialize a local devnet: generate genesis + per-validator keys.

    Calls ``tempo-xtask generate-localnet`` under the hood, then renames
    validator directories to moniker-based names and generates a supervisor
    configuration file (``supervisord.ini``) referencing each node.

    Args:
        data: Path to the root data directory.
        config: Path to the YAML configuration file.
        force: Overwrite existing data directory.
        **kwargs: Additional overrides for config fields (e.g. ``chain_id=1337``).
    """
    data_dir = Path(data).resolve()

    # Load configuration
    cfg = DevnetConfig.load(config)

    # Apply CLI overrides
    if kwargs:
        for k, v in kwargs.items():
            if hasattr(cfg, k) and v is not None:
                setattr(cfg, k, v)

    _ensure_tempo_bins(cfg)

    # Prepare data directory
    if data_dir.exists():
        if force:
            shutil.rmtree(data_dir)
            data_dir.mkdir(parents=True)
        else:
            print(f"Data directory {data_dir} already exists. Use --force to overwrite.", file=sys.stderr)
            sys.exit(1)
    else:
        data_dir.mkdir(parents=True)

    # Build and run tempo-xtask generate-localnet
    xtask_args = [cfg.tempo_xtask_bin, "generate-localnet", "--output", str(data_dir), "--force"]

    # Forward genesis args
    xtask_args.extend(cfg.to_genesis_args())

    print(f"Running: {' '.join(xtask_args)}")
    result = subprocess.run(xtask_args, capture_output=False)
    if result.returncode != 0:
        print(f"Error: tempo-xtask failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(1)

    # Rename validator dirs from ip:port to moniker names
    _rename_validator_dirs(data_dir, cfg)

    # Save a copy of the config in the data directory for later use
    config_dst = data_dir / "devnet.yaml"
    with open(config_dst, "w") as f:
        yaml.dump(cfg.to_dict(), f, default_flow_style=False)

    # Generate supervisor config
    sup_dst = generate_supervisor_config(cfg, data_dir, force=True)
    print(f"\nSupervisor config written to: {sup_dst}")
    print(f"Genesis written to: {data_dir / 'genesis.json'}")
    print(f"\nReady. Run: tempo-devnet start --data {data}")


def start(data: str = "./data") -> None:
    """Start the supervisor daemon in the foreground.

    All node logs are printed to stdout. Press Ctrl+C to stop
    the entire cluster.

    Args:
        data: Path to the root data directory (same as ``--data`` in ``init``).
    """
    data_dir = Path(data).resolve()
    tasks_ini = data_dir / SUPERVISOR_CONFIG_FILE

    if not tasks_ini.exists():
        print(f"Error: {tasks_ini} not found. Run `tempo-devnet init` first.", file=sys.stderr)
        sys.exit(1)

    print("Starting devnet (supervisord) — Ctrl+C to stop all nodes...")
    print(f"  data: {data_dir}")
    print()

    os.execvp(
        sys.executable,
        [sys.executable, "-m", "supervisor.supervisord", "-c", str(tasks_ini)],
    )


def status(data: str = "./data") -> None:
    """Print a human-readable cluster status summary.

    Args:
        data: Path to the root data directory.
    """
    cli = ClusterCLI(data)
    print(cli.summary())


def supervisorctl(*args: str, data: str = "./data") -> None:
    """Run ``supervisorctl`` commands against the running cluster.

    Without arguments, opens an interactive shell.

    Examples::

        tempo-devnet supervisorctl status
        tempo-devnet supervisorctl tail node0
        tempo-devnet supervisorctl restart node0

    Args:
        *args: Supervisorctl arguments (e.g. ``status``, ``tail node0``).
        data: Path to the root data directory.
    """
    tasks_ini = Path(data).resolve() / SUPERVISOR_CONFIG_FILE
    if not tasks_ini.exists():
        print(f"Error: {tasks_ini} not found. Is the cluster initialized?", file=sys.stderr)
        sys.exit(1)

    supervisorctl_main(("-c", str(tasks_ini), *args))


class CLI:
    """tempo-devnet — Multi-node Tempo testnet manager.

    Manage a local Tempo testnet cluster from a single YAML config file,
    using supervisord to run all node processes.
    """

    def init(
        self,
        data: str = "./data",
        config: str = "./devnet.yaml",
        force: bool = False,
        **kwargs: str | int | bool | None,
    ) -> None:
        """Prepare genesis, keys, and supervisor config."""
        init(data, config, force, **kwargs)

    def start(self, data: str = "./data") -> None:
        """Start the supervisor daemon (all nodes)."""
        start(data)

    def serve(
        self,
        data: str = "./data",
        config: str = "./devnet.yaml",
        force: bool = False,
        **kwargs: str | int | bool | None,
    ) -> None:
        """Initialize and start the cluster (init + start)."""
        init(data, config, force, **kwargs)
        start(data)

    def status(self, data: str = "./data") -> None:
        """Show cluster status summary."""
        status(data)

    def supervisorctl(self, *args: str, data: str = "./data") -> None:
        """Run supervisorctl (interactive or with args)."""
        supervisorctl(*args, data=data)


def main() -> None:
    """
    tempo-devnet — Multi-node Tempo testnet manager.

    Manage a local Tempo testnet cluster from a single YAML config file,
    using supervisord to run all node processes.

    Examples:

        tempo-devnet serve --data ./data --config ./devnet.yaml
        tempo-devnet init --data ./data --config ./devnet.yaml
        tempo-devnet start --data ./data
        tempo-devnet status --data ./data
        tempo-devnet supervisorctl status
    """
    fire.Fire(CLI)


if __name__ == "__main__":
    main()
