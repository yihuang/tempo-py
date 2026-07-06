"""Port calculation for multi-node testnet.

Each node gets a ``base_port``. Service ports are derived as offsets::

    ===================  ============  =======================
    Role                 Offset        Flag
    ===================  ============  =======================
    Consensus P2P        +0            ``--consensus.listen-address``
    Execution P2P        +1            ``--port`` / ``--discovery.port``
    Consensus Metrics    +2            ``--consensus.metrics-address``
    Engine API (authrpc) +3            ``--authrpc.port``
    HTTP JSON-RPC        +4            ``--http.port``
    WebSocket            +5            ``--ws.port``
    ===================  ============  =======================
"""

import socket


def consensus_p2p_port(base_port: int) -> int:
    """Port for ``--consensus.listen-address`` (P2P consensus)."""
    return base_port


def execution_p2p_port(base_port: int) -> int:
    """Port for ``--port`` / ``--discovery.port`` (execution layer P2P)."""
    return base_port + 1


def consensus_metrics_port(base_port: int) -> int:
    """Port for ``--consensus.metrics-address``."""
    return base_port + 2


def authrpc_port(base_port: int) -> int:
    """Port for ``--authrpc.port`` (engine API)."""
    return base_port + 3


def http_rpc_port(base_port: int) -> int:
    """Port for ``--http.port`` (JSON-RPC HTTP)."""
    return base_port + 4


def ws_rpc_port(base_port: int) -> int:
    """Port for ``--ws.port`` (JSON-RPC WebSocket)."""
    return base_port + 5


PORTS_PER_NODE = 6
"""Number of derived service ports per node (offsets 0..5 above)."""


def _can_bind(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def find_free_base_ports(count: int, *, stride: int = 10, host: str = "127.0.0.1") -> list[int]:
    """Reserve ``count`` free base-port blocks for an ephemeral devnet.

    Useful for tests and CI, where fixed base ports (8000, 8010, ...) would
    collide between parallel runs or with other local nodes. Every derived
    service port (offsets 0..5) of every block is probed to be bindable.
    """
    for _ in range(200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            base = s.getsockname()[1]
        bases = [base + i * stride for i in range(count)]
        needed = [p + k for p in bases for k in range(PORTS_PER_NODE)]
        if max(needed) < 65500 and all(_can_bind(host, p) for p in needed):
            return bases
    raise RuntimeError("could not find a free devnet port region")
