"""Tests for the tempo-devnet module."""

from __future__ import annotations

import os
import socket
import stat
import tempfile
from pathlib import Path

import json
import jsonmerge
import tomlkit
import yaml

from tempo.devnet.cluster import ClusterCLI

from tempo.devnet.config import DevnetConfig, ValidatorConfig
from tempo.devnet.ports import (
    PORTS_PER_NODE,
    authrpc_port,
    find_free_base_ports,
    consensus_metrics_port,
    consensus_p2p_port,
    execution_p2p_port,
    http_rpc_port,
    ws_rpc_port,
)
from tempo.devnet.supervisor import (
    LOCALNET_SIGNING_KEY_SECRET,
    apply_genesis_patch,
    generate_supervisor_config,
    write_run_script,
    write_reth_config,
    write_secret_file,
    _build_node_args,
    _sh_quote,
)


class TestPorts:
    """Verify port offset calculations."""

    def test_consensus_p2p(self) -> None:
        assert consensus_p2p_port(8000) == 8000

    def test_execution_p2p(self) -> None:
        assert execution_p2p_port(8000) == 8001

    def test_consensus_metrics(self) -> None:
        assert consensus_metrics_port(8000) == 8002

    def test_authrpc(self) -> None:
        assert authrpc_port(8000) == 8003

    def test_http_rpc(self) -> None:
        assert http_rpc_port(8000) == 8004

    def test_ws_rpc(self) -> None:
        assert ws_rpc_port(8000) == 8005

    def test_all_offsets_unique(self) -> None:
        """All port functions should return distinct values for the same base."""
        base = 8000
        ports = {
            consensus_p2p_port(base),
            execution_p2p_port(base),
            consensus_metrics_port(base),
            authrpc_port(base),
            http_rpc_port(base),
            ws_rpc_port(base),
        }
        assert len(ports) == 6


class TestValidatorConfig:
    """Validator config parsing and serialization."""

    def test_default_moniker(self) -> None:
        v = ValidatorConfig(port=8000)
        assert v.host == "127.0.0.1"
        assert v.port == 8000
        assert v.moniker == "node0"

    def test_custom_moniker(self) -> None:
        v = ValidatorConfig(host="10.0.0.1", port=9000, moniker="alice")
        assert v.host == "10.0.0.1"
        assert v.moniker == "alice"

    def test_dir_name_is_moniker(self) -> None:
        v = ValidatorConfig(host="10.0.0.1", port=9000, moniker="alice")
        assert v.dir_name == "alice"
        v2 = ValidatorConfig(port=8000)
        assert v2.dir_name == "node0"

    def test_to_validator_arg(self) -> None:
        v = ValidatorConfig(host="10.0.0.1", port=9000)
        assert v.to_validator_arg() == "10.0.0.1:9000"

    def test_addr_str(self) -> None:
        v = ValidatorConfig(host="10.0.0.1", port=9000)
        assert v.addr_str == "10.0.0.1:9000"

    def test_from_dict(self) -> None:
        d = {"host": "10.0.0.2", "port": 9001, "moniker": "bob"}
        v = ValidatorConfig.from_dict(d)
        assert v.host == "10.0.0.2"
        assert v.port == 9001
        assert v.moniker == "bob"

    def test_base_port_explicit(self) -> None:
        v = ValidatorConfig(host="127.0.0.1", port=8000, base_port=9000)
        assert v.base_port == 9000

    def test_base_port_auto(self) -> None:
        v = ValidatorConfig(host="127.0.0.1", port=8000)
        assert v.base_port == 8000

    def test_to_dict_roundtrip(self) -> None:
        v1 = ValidatorConfig(host="10.0.0.1", port=9000, moniker="node1")
        v2 = ValidatorConfig.from_dict(v1.to_dict())
        assert v1.host == v2.host
        assert v1.port == v2.port
        assert v1.moniker == v2.moniker
        assert v1.base_port == v2.base_port


class TestDevnetConfig:
    """Full config loading, parsing, and CLI arg generation."""

    def test_default_validators(self) -> None:
        cfg = DevnetConfig({"chain_id": 1337})
        assert len(cfg.validators) == 4
        assert cfg.validators[0].port == 8000
        assert cfg.validators[3].port == 8030

    def test_custom_validators(self) -> None:
        data = {
            "chain_id": 42,
            "validators": [
                {"host": "10.0.0.1", "port": 9000, "moniker": "alpha"},
                {"host": "10.0.0.2", "port": 9001, "moniker": "beta"},
            ],
        }
        cfg = DevnetConfig(data)
        assert cfg.chain_id == 42
        assert len(cfg.validators) == 2
        assert cfg.validators[0].moniker == "alpha"
        assert cfg.validators[1].moniker == "beta"

    def test_validators_arg(self) -> None:
        cfg = DevnetConfig(
            {
                "validators": [
                    {"host": "10.0.0.1", "port": 9000},
                    {"host": "10.0.0.2", "port": 9001},
                ],
            }
        )
        assert cfg.validators_arg == "10.0.0.1:9000,10.0.0.2:9001"

    def test_to_genesis_args(self) -> None:
        cfg = DevnetConfig(
            {
                "chain_id": 1337,
                "accounts": 5000,
                "epoch_length": 200,
                "gas_limit": 100_000_000,
                "seed": 42,
                "validators": [{"host": "127.0.0.1", "port": 8000}],
            }
        )
        args = cfg.to_genesis_args()
        assert "--chain-id" in args
        assert "1337" in args
        assert "--seed" in args
        assert "42" in args
        assert "127.0.0.1:8000" in args

    def test_load_yaml(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {
                    "chain_id": 999,
                    "validators": [{"host": "10.0.0.1", "port": 9000}],
                },
                f,
            )
            path = f.name

        try:
            cfg = DevnetConfig.load(path)
            assert cfg.chain_id == 999
            assert len(cfg.validators) == 1
        finally:
            os.unlink(path)

    def test_to_dict_roundtrip(self) -> None:
        data = {
            "chain_id": 1337,
            "accounts": 5000,
            "seed": 42,
            "no_dkg_in_genesis": True,
            "validators": [{"host": "127.0.0.1", "port": 8000}],
        }
        cfg1 = DevnetConfig(data)
        cfg2 = DevnetConfig(cfg1.to_dict())
        assert cfg1.chain_id == cfg2.chain_id
        assert cfg1.accounts == cfg2.accounts
        assert cfg1.seed == cfg2.seed
        assert cfg1.no_dkg_in_genesis == cfg2.no_dkg_in_genesis
        assert len(cfg1.validators) == len(cfg2.validators)

    def test_hardfork_timestamps(self) -> None:
        cfg = DevnetConfig(
            {
                "t1_time": 100,
                "t6_time": 200,
            }
        )
        assert cfg.t1_time == 100
        assert cfg.t6_time == 200
        assert cfg.t0_time == 0  # default

        args = cfg.to_genesis_args()
        assert "--t1-time" in args
        assert "100" in args
        assert "--t6-time" in args
        assert "200" in args


class TestBuildNodeArgs:
    """_build_node_args — argument list construction."""

    def test_returns_list(self) -> None:
        args = _build_node_args(
            tempo_bin="tempo",
            host="127.0.0.1",
            base_port=8000,
            genesis_path="/g.json",
            datadir="/d",
            signing_key="/d/signing.key",
            signing_share="/d/signing.share",
            secret_file="/d/.secret",
            enode_key="/d/enode.key",
            trusted_peers=["enode://abc@127.0.0.1:8001"],
        )
        assert isinstance(args, list)
        assert args[0] == "tempo"
        assert args[1] == "node"

    def test_port_values(self) -> None:
        args = _build_node_args(
            tempo_bin="tempo",
            host="127.0.0.1",
            base_port=8000,
            genesis_path="/g.json",
            datadir="/d",
            signing_key="/d/signing.key",
            signing_share="/d/signing.share",
            secret_file="/d/.secret",
            enode_key="/d/enode.key",
            trusted_peers=[],
        )
        assert "--consensus.listen-address" in args
        assert "127.0.0.1:8000" in args
        assert "--port" in args
        assert "8001" in args  # base+1
        assert "--consensus.metrics-address" in args
        assert "127.0.0.1:8002" in args  # base+2
        assert "--authrpc.port" in args
        assert "8003" in args  # base+3
        assert "--http.port" in args
        assert "8004" in args  # base+4
        assert "--ws.port" in args
        assert "8005" in args  # base+5

    def test_uses_secret_file(self) -> None:
        args = _build_node_args(
            tempo_bin="tempo",
            host="127.0.0.1",
            base_port=8000,
            genesis_path="/g.json",
            datadir="/d",
            signing_key="/d/signing.key",
            signing_share="/d/signing.share",
            secret_file="/d/.secret",
            enode_key="/d/enode.key",
            trusted_peers=["enode://abc@127.0.0.1:8001"],
        )
        assert "--consensus.secret" in args
        assert "/d/.secret" in args
        assert "<(" not in args


class TestWriteRunScript:
    """Wrapper script generation."""

    def test_writes_executable_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            val_dir = Path(tmpdir) / "node0"
            val_dir.mkdir(parents=True)
            node_args = _build_node_args(
                tempo_bin="tempo",
                host="127.0.0.1",
                base_port=8000,
                genesis_path="/g.json",
                datadir="/d",
                signing_key="/d/signing.key",
                signing_share="/d/signing.share",
                secret_file="/d/.secret",
                enode_key="/d/enode.key",
                trusted_peers=["enode://abc@127.0.0.1:8001"],
            )

            script_path = write_run_script(val_dir, node_args)
            assert script_path == val_dir / "run.sh"
            assert script_path.exists()

            # Check executable
            st = script_path.stat()
            assert st.st_mode & stat.S_IXUSR

            # Check content
            content = script_path.read_text()
            assert content.startswith("#!/usr/bin/env bash")
            assert "exec " in content
            assert "'tempo'" in content
            assert "'node'" in content
            assert "'--consensus.secret'" in content
            # Should use shell-quoted args
            assert "set -o errexit" in content
            assert "set -o nounset" in content

    def test_arg_quoting(self) -> None:
        """Arguments with special chars are properly shell-quoted."""
        result = _sh_quote("/path/to/file")
        assert result == "'/path/to/file'"

    def test_arg_quoting_with_apostrophe(self) -> None:
        result = _sh_quote("it's a test")
        assert "'" in result


class TestWriteSecretFile:
    """Writing the signing key passphrase file."""

    def test_writes_secret_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            val_dir = Path(tmpdir) / "node0"
            val_dir.mkdir(parents=True)
            secret_path = write_secret_file(val_dir)
            assert secret_path == val_dir / ".secret"
            assert secret_path.exists()
            assert secret_path.read_text() == LOCALNET_SIGNING_KEY_SECRET


class TestSupervisorConfig:
    """Supervisor config file generation."""

    def test_generate_config(self) -> None:
        cfg = DevnetConfig(
            {
                "chain_id": 1337,
                "validators": [
                    {"host": "127.0.0.1", "port": 8000, "moniker": "node0"},
                    {"host": "127.0.0.1", "port": 8010, "moniker": "node1"},
                ],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create validator dirs named by moniker (as they'd be after rename)
            for val in cfg.validators:
                val_dir = data_dir / val.dir_name
                val_dir.mkdir(parents=True)
                (val_dir / "enode.identity").write_text("abc123")
                (val_dir / "signing.key").write_text("dummy")
                (val_dir / "signing.share").write_text("dummy")
                (val_dir / "enode.key").write_text("dummy")

            (data_dir / "genesis.json").write_text("{}")

            # Generate supervisor config
            dst = generate_supervisor_config(cfg, data_dir)

            assert dst.exists()
            content = dst.read_text()

            # Check basic structure
            assert "[supervisord]" in content
            assert "[unix_http_server]" in content
            assert "file = " in content

            # Program names should use moniker
            assert "[program:node0]" in content
            assert "[program:node1]" in content

            # Command should point to the wrapper script (absolute path), not inline the full command
            assert "command = " in content
            assert "/run.sh" in content

            # Secret files should be written
            for val in cfg.validators:
                secret_path = data_dir / val.dir_name / ".secret"
                assert secret_path.exists(), f"secret file not found at {secret_path}"
                assert secret_path.read_text() == LOCALNET_SIGNING_KEY_SECRET

            # Wrapper scripts should be written
            for val in cfg.validators:
                run_script = data_dir / val.dir_name / "run.sh"
                assert run_script.exists(), f"run.sh not found at {run_script}"
                script_content = run_script.read_text()
                assert "exec " in script_content
                assert "'tempo'" in script_content
                assert "'--consensus.secret'" in script_content

    def test_node_logging_in_config(self) -> None:
        cfg = DevnetConfig(
            {
                "validators": [
                    {"host": "127.0.0.1", "port": 8000, "moniker": "node0"},
                ],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            val_dir = data_dir / "node0"
            val_dir.mkdir(parents=True)
            (val_dir / "enode.identity").write_text("abc")
            (val_dir / "signing.key").write_text("k")
            (val_dir / "signing.share").write_text("s")
            (val_dir / "enode.key").write_text("e")
            (data_dir / "genesis.json").write_text("{}")

            dst = generate_supervisor_config(cfg, data_dir)
            content = dst.read_text()
            assert "stdout_logfile" in content
            assert "node0/node.log" in content
            # redirect_stderr=true makes stderr_logfile redundant; should not appear
            assert "stderr_logfile" not in content


class TestClusterCLI:
    """ClusterCLI instantiation and summary."""

    def test_node_dirs_by_moniker(self) -> None:

        cfg = DevnetConfig(
            {
                "validators": [
                    {"host": "127.0.0.1", "port": 8000, "moniker": "node0"},
                    {"host": "127.0.0.1", "port": 8010, "moniker": "node1"},
                ],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            for val in cfg.validators:
                (data_dir / val.dir_name).mkdir(parents=True)
            (data_dir / "genesis.json").write_text("{}")

            cli = ClusterCLI(data_dir, config=cfg)
            dirs = cli.node_dirs()
            assert len(dirs) == 2
            # Dirs should be named by moniker
            assert all(d.name in ("node0", "node1") for d in dirs)

    def test_rpc_urls_by_moniker(self) -> None:

        cfg = DevnetConfig(
            {
                "validators": [
                    {"host": "127.0.0.1", "port": 8000, "moniker": "alice"},
                ],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            (data_dir / "alice").mkdir(parents=True)
            (data_dir / "genesis.json").write_text("{}")

            cli = ClusterCLI(data_dir, config=cfg)
            url = cli.node_rpc_url("alice")
            assert "8004" in url  # http_rpc_port(8000) = 8004

            ws_url = cli.node_ws_url("alice")
            assert "8005" in ws_url  # ws_rpc_port(8000) = 8005

    def test_rpc_urls_by_moniker_lookup(self) -> None:

        cfg = DevnetConfig(
            {
                "validators": [
                    {"host": "127.0.0.1", "port": 8000, "moniker": "node0"},
                    {"host": "127.0.0.1", "port": 8010, "moniker": "node1"},
                ],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            for val in cfg.validators:
                (data_dir / val.dir_name).mkdir(parents=True)
            (data_dir / "genesis.json").write_text("{}")

            cli = ClusterCLI(data_dir, config=cfg)
            assert "8004" in cli.node_rpc_url("node0")
            assert "8014" in cli.node_rpc_url("node1")
            assert "8005" in cli.node_ws_url("node0")
            assert "8015" in cli.node_ws_url("node1")


class TestPatches:
    """Genesis patching, reth config, and node flag overrides."""

    def test_jsonmerge_simple(self) -> None:

        base = {"a": 1, "b": 2}
        merged = jsonmerge.merge(base, {"b": 3, "c": 4})
        assert merged == {"a": 1, "b": 3, "c": 4}

    def test_jsonmerge_nested(self) -> None:

        base = {"a": {"x": 1, "y": 2}, "b": 3}
        merged = jsonmerge.merge(base, {"a": {"y": 99, "z": 100}})
        assert merged == {"a": {"x": 1, "y": 99, "z": 100}, "b": 3}

    def test_apply_genesis_patch(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            (data_dir / "genesis.json").write_text(
                json.dumps({"chain_id": 1337, "config": {"extra_fields": {"epochLength": 100}}})
            )

            apply_genesis_patch(
                data_dir,
                {"config": {"extra_fields": {"epochLength": 50}, "someNewField": "hello"}},
            )

            updated = json.loads((data_dir / "genesis.json").read_text())
            assert updated["chain_id"] == 1337  # unchanged
            assert updated["config"]["extra_fields"]["epochLength"] == 50  # overridden
            assert updated["config"]["someNewField"] == "hello"  # added

    def test_apply_genesis_patch_noop_if_empty(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            (data_dir / "genesis.json").write_text("{}")
            apply_genesis_patch(data_dir, {})
            assert (data_dir / "genesis.json").read_text() == "{}"

    def test_write_reth_config(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            val_dir = Path(tmpdir) / "node0"
            val_dir.mkdir(parents=True)

            write_reth_config(
                val_dir,
                {"p2p": {"max_inbound": 50}, "db": {"max_size": "8TB"}},
            )

            reth_path = val_dir / "reth.toml"
            assert reth_path.exists()

            with open(reth_path) as f:
                parsed = tomlkit.load(f)
            assert parsed["p2p"]["max_inbound"] == 50
            assert parsed["db"]["max_size"] == "8TB"

    def test_write_reth_config_noop_if_empty(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            val_dir = Path(tmpdir) / "node0"
            val_dir.mkdir(parents=True)
            write_reth_config(val_dir, {})
            assert not (val_dir / "reth.toml").exists()

    def test_extra_flags_in_node_args(self) -> None:
        args = _build_node_args(
            tempo_bin="tempo",
            host="127.0.0.1",
            base_port=8000,
            genesis_path="/g.json",
            datadir="/d",
            signing_key="/d/signing.key",
            signing_share="/d/signing.share",
            secret_file="/d/.secret",
            enode_key="/d/enode.key",
            trusted_peers=[],
            extra_flags=["--txpool.max-tempo-authorizations", "32"],
        )
        assert "--txpool.max-tempo-authorizations" in args
        idx = args.index("--txpool.max-tempo-authorizations")
        assert args[idx + 1] == "32"

    def test_patch_node_flags_in_config_roundtrip(self) -> None:
        cfg = DevnetConfig(
            {
                "validators": [{"host": "127.0.0.1", "port": 8000, "moniker": "n0"}],
                "patch_node_flags": ["--some-flag", "value"],
            }
        )
        assert cfg.patch_node_flags == ["--some-flag", "value"]

    def test_patch_genesis_in_config_roundtrip(self) -> None:
        cfg = DevnetConfig(
            {
                "validators": [{"host": "127.0.0.1", "port": 8000, "moniker": "n0"}],
                "patch_genesis": {"config": {"extra_fields": {"epochLength": 50}}},
            }
        )
        assert cfg.patch_genesis["config"]["extra_fields"]["epochLength"] == 50
        d = cfg.to_dict()
        assert "patch_genesis" in d

    def test_patch_reth_in_config_roundtrip(self) -> None:
        cfg = DevnetConfig(
            {
                "validators": [{"host": "127.0.0.1", "port": 8000, "moniker": "n0"}],
                "patch_reth": {"p2p": {"max_inbound": 50}},
            }
        )
        assert cfg.patch_reth["p2p"]["max_inbound"] == 50
        d = cfg.to_dict()
        assert "patch_reth" in d

    def test_generate_config_with_all_patches(self) -> None:
        cfg = DevnetConfig(
            {
                "validators": [
                    {"host": "127.0.0.1", "port": 8000, "moniker": "node0"},
                ],
                "patch_genesis": {"nonce": "0x42"},
                "patch_reth": {"p2p": {"max_inbound": 50}},
                "patch_node_flags": ["--txpool.max-tempo-authorizations", "32"],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            val_dir = data_dir / "node0"
            val_dir.mkdir(parents=True)
            (val_dir / "enode.identity").write_text("abc")
            (val_dir / "signing.key").write_text("k")
            (val_dir / "signing.share").write_text("s")
            (val_dir / "enode.key").write_text("e")

            (data_dir / "genesis.json").write_text(json.dumps({"chain_id": 1337}))

            generate_supervisor_config(cfg, data_dir, force=True)

            # Genesis patch applied
            genesis = json.loads((data_dir / "genesis.json").read_text())
            assert genesis["nonce"] == "0x42"

            # Reth config written
            assert (val_dir / "reth.toml").exists()
            with open(val_dir / "reth.toml") as f:
                reth_parsed = tomlkit.load(f)
            assert reth_parsed["p2p"]["max_inbound"] == 50

            # Extra flags in run.sh
            run_sh = (val_dir / "run.sh").read_text()
            assert "--txpool.max-tempo-authorizations" in run_sh
            assert "'32'" in run_sh


class TestFindFreeBasePorts:
    def test_returns_spaced_bindable_blocks(self):
        bases = find_free_base_ports(4)
        assert len(bases) == 4
        assert all(bases[i + 1] - bases[i] == 10 for i in range(3))  # default stride
        # every derived service port in every block is currently bindable
        for base in bases:
            for offset in range(PORTS_PER_NODE):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", base + offset))

    def test_blocks_stay_under_the_port_ceiling(self):
        assert max(find_free_base_ports(4)) + PORTS_PER_NODE < 65536
