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
