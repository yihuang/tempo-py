"""JSON-RPC client for interacting with the Tempo blockchain."""

from __future__ import annotations

from typing import Any, Optional
from urllib.request import Request, urlopen

from eth_utils import to_bytes

from .constants import NONCE_ADDRESS


class JSONRPCRequest:
    """JSON-RPC 2.0 request."""

    def __init__(
        self,
        method: str,
        params: Optional[list[object]] = None,
        request_id: int = 1,
    ) -> None:
        self.jsonrpc = "2.0"
        self.id = request_id
        self.method = method
        self.params = params or []

    def to_dict(self) -> dict[str, object]:
        return {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }


class JSONRPCError(Exception):
    """Raised when the RPC returns an error response."""

    def __init__(self, code: int, message: str, data: Optional[object] = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"RPC error {code}: {message}")


class Client:
    """HTTP JSON-RPC client for Tempo.

    Args:
        rpc_url: Tempo RPC endpoint URL.
        auth: Optional ``(username, password)`` tuple for basic auth.
        timeout: HTTP request timeout in seconds (default 30).
    """

    def __init__(
        self,
        rpc_url: str,
        auth: Optional[tuple[str, str]] = None,
        timeout: int = 30,
    ) -> None:
        self.rpc_url = rpc_url
        self.auth = auth
        self.timeout = timeout
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _build_request(
        self, method: str, params: Optional[list[object]] = None
    ) -> Request:
        import json

        req = JSONRPCRequest(method, params, self._next_id())
        body = json.dumps(req.to_dict()).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        r = Request(self.rpc_url, data=body, headers=headers, method="POST")
        if self.auth:
            import base64

            user, pwd = self.auth
            encoded = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            r.add_header("Authorization", f"Basic {encoded}")
        return r

    def _send(
        self, method: str, params: Optional[list[object]] = None
    ) -> dict[str, object]:
        import json

        req = self._build_request(method, params)
        resp = urlopen(req, timeout=self.timeout)
        data = json.loads(resp.read().decode("utf-8"))
        if "error" in data and data["error"] is not None:
            err = data["error"]
            raise JSONRPCError(
                code=err.get("code", 0),
                message=err.get("message", "unknown error"),
                data=err.get("data"),
            )
        return data

    def send_request(
        self, method: str, params: Optional[list[object]] = None
    ) -> dict[str, object]:
        """Send a generic JSON-RPC request."""
        return self._send(method, params)

    # ------------------------------------------------------------------
    # Chain queries
    # ------------------------------------------------------------------

    def get_block_number(self) -> int:
        """Get the current block number."""
        data = self._send("eth_blockNumber")
        result = data.get("result", "0x0")
        return int(str(result), 16) if isinstance(result, str) else 0

    def get_transaction_count(self, address: str) -> int:
        """Get the nonce for an address (nonce key 0)."""
        data = self._send("eth_getTransactionCount", [address, "pending"])
        result = data.get("result", "0x0")
        return int(str(result), 16) if isinstance(result, str) else 0

    def get_nonce(self, address: str, nonce_key: int = 0) -> int:
        """Get the nonce for an address and nonce key (2D nonce system).

        Calls the NonceManager precompile.
        """
        # Build calldata: getNonce(address,uint256) selector
        get_nonce_selector = "0x89535803"
        padded_addr = address[2:].zfill(64) if address.startswith("0x") else address.zfill(64)
        padded_key = hex(nonce_key)[2:].zfill(64)
        call_data = get_nonce_selector + padded_addr + padded_key

        call_obj = {"to": NONCE_ADDRESS, "data": call_data}
        data = self._send("eth_call", [call_obj, "latest"])
        result = data.get("result", "0x0")
        return int(str(result), 16) if isinstance(result, str) else 0

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    def send_raw_transaction(self, serialized_tx: str) -> str:
        """Broadcast a raw transaction and return the tx hash."""
        data = self._send("eth_sendRawTransaction", [serialized_tx])
        return str(data.get("result", ""))

    def send_raw_transaction_sync(self, serialized_tx: str) -> str:
        """Broadcast a raw transaction synchronously (waits for inclusion)."""
        data = self._send("eth_sendRawTransactionSync", [serialized_tx])
        return str(data.get("result", ""))

    def get_transaction_receipt(self, tx_hash: str) -> Optional[dict[str, object]]:
        """Fetch a transaction receipt. Returns None if not yet mined."""
        try:
            data = self._send("eth_getTransactionReceipt", [tx_hash])
        except JSONRPCError:
            return None
        result = data.get("result")
        if result is None:
            return None
        if isinstance(result, dict):
            return result
        return None

    def get_gas_price(self) -> int:
        """Get the current gas price."""
        data = self._send("eth_gasPrice")
        result = data.get("result", "0x0")
        return int(str(result), 16) if isinstance(result, str) else 0

    def get_chain_id(self) -> int:
        """Get the chain ID."""
        data = self._send("eth_chainId")
        result = data.get("result", "0x0")
        return int(str(result), 16) if isinstance(result, str) else 0

    def estimate_gas(self, tx_dict: dict[str, object]) -> int:
        """Estimate gas for a transaction."""
        data = self._send("eth_estimateGas", [tx_dict])
        result = data.get("result", "0x0")
        return int(str(result), 16) if isinstance(result, str) else 0

    def call(self, call_dict: dict[str, object], block: str = "latest") -> str:
        """Execute a read-only call."""
        data = self._send("eth_call", [call_dict, block])
        return str(data.get("result", ""))
