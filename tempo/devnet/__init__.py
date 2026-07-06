"""tempo-devnet — Multi-node testnet generator with supervisord.

Manages local testnets from a single YAML config file, similar to
`pystarport <https://github.com/crypto-com/pystarport>`_.

Workflow::

    # Write config.yaml, then:
    tempo-devnet serve --data ./data --config ./devnet.yaml

Or step by step::

    tempo-devnet init --data ./data --config ./devnet.yaml
    tempo-devnet start --data ./data
    tempo-devnet supervisorctl status

Port scheme per node (base_port is auto-assigned or from config):

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

from .cli import CLI
from .cluster import ClusterCLI

__all__ = [
    "CLI",
    "ClusterCLI",
]
