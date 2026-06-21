"""
Deltex synchronous Python client.
"""

from __future__ import annotations

import os
import re
import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

__version__ = "1.3.3"
SDK_VERSION = __version__


# ─── Types ────────────────────────────────────────────────────────────────────

Row = Dict[str, Any]
Param = Any  # str | int | float | bool | None | dict | list


@dataclass
class QueryResult:
    """Full result envelope from a Deltex query."""
    rows: List[Row]
    columns: List[str]
    rows_affected: int
    execution_ms: Optional[float] = None
    commit_status: Optional[str] = None   # "committed" | "edge-accepted" | "async-queued"
    schema_version: Optional[int] = None


# ─── Errors ───────────────────────────────────────────────────────────────────

class DeltexError(Exception):
    """Raised for engine errors or network failures."""
    def __init__(self, message: str, status: int = 0, sql: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.sql = sql
        self.engine_message = message


class RateLimitError(DeltexError):
    """Raised when the rate limit (200 req/min) is exceeded after all retries."""
    def __init__(self, retry_after: float, sql: Optional[str] = None):
        super().__init__(f"Rate limit exceeded. Retry after {retry_after}s.", 429, sql)
        self.retry_after = retry_after


# ─── Parameter binding ────────────────────────────────────────────────────────

_POSITIONAL_RE = re.compile(r"\$(\d+)")
_SINGLE_QUOTE_RE = re.compile(r"'")


def _format_param(v: Param) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not (v == v):  # NaN
            raise DeltexError("NaN is not a valid SQL parameter")
        return repr(v)
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    # dict/list: JSON-encode
    return "'" + json.dumps(v).replace("'", "''") + "'"


def _bind(sql: str, params: Sequence[Param]) -> str:
    if not params:
        return sql
    def replace(m: re.Match) -> str:
        idx = int(m.group(1)) - 1
        if idx < 0 or idx >= len(params):
            raise DeltexError(f"Missing SQL parameter ${m.group(1)} ({len(params)} provided)")
        return _format_param(params[idx])
    return _POSITIONAL_RE.sub(replace, sql)


# ─── HTTP ─────────────────────────────────────────────────────────────────────

_TIMING_RE = re.compile(r"total;dur=([\d.]+)")
_COMMIT_STATUS_VALUES = {"committed", "edge-accepted", "async-queued"}


def _do_request(url: str, headers: Dict[str, str], body: bytes, timeout: float) -> urllib.request.Request:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    return req


def _run_query(sql: str, url: str, headers: Dict[str, str], timeout: float, max_retries: int) -> QueryResult:
    body = json.dumps({"sql": sql}).encode()
    last_err: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                resp_headers = dict(resp.headers)
                status = resp.status
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = float(e.headers.get("retry-after", "1") or "1") or 1.0
                if attempt < max_retries:
                    last_err = RateLimitError(retry_after, sql)
                    time.sleep(retry_after)
                    continue
                raise RateLimitError(retry_after, sql)
            try:
                body_err = json.loads(e.read())
                msg = body_err.get("message") or body_err.get("error") or str(e)
            except Exception:
                msg = str(e)
            raise DeltexError(msg, e.code, sql)
        except Exception as e:
            raise DeltexError(f"Network error: {e}", 0, sql)

        try:
            data = json.loads(raw)
        except Exception:
            raise DeltexError(f"Invalid JSON response (HTTP {status})", status, sql)

        if data.get("success") is False or (status >= 400 and "columns" not in data):
            msg = data.get("message") or data.get("error") or "Unknown engine error"
            raise DeltexError(str(msg), status, sql)

        # Parse server-timing
        execution_ms: Optional[float] = None
        st = resp_headers.get("server-timing", resp_headers.get("Server-Timing", ""))
        m = _TIMING_RE.search(st)
        if m:
            execution_ms = float(m.group(1))

        # Deltex-specific headers
        raw_status = (resp_headers.get("x-commit-status") or resp_headers.get("X-Commit-Status") or "").strip()
        commit_status = raw_status if raw_status in _COMMIT_STATUS_VALUES else None

        raw_schema = (resp_headers.get("x-schema-version") or resp_headers.get("X-Schema-Version") or "").strip()
        schema_version: Optional[int] = int(raw_schema) if raw_schema.isdigit() else None

        columns = data.get("columns") or []
        rows = data.get("rows") or []
        rows_affected = data.get("affected_rows") or data.get("rows_affected") or data.get("affected") or len(rows)

        return QueryResult(
            rows=rows,
            columns=columns,
            rows_affected=rows_affected,
            execution_ms=execution_ms,
            commit_status=commit_status,
            schema_version=schema_version,
        )

    raise (last_err or DeltexError("Retry loop exhausted", 429, sql))


# ─── Client ───────────────────────────────────────────────────────────────────

class Client:
    """
    Deltex synchronous SQL client.

    Example:
        db = deltex.connect()
        users = db.query("SELECT * FROM users WHERE active = $1", [True])
        user  = db.query_one("SELECT * FROM users WHERE id = $1", [42])
        n     = db.execute("INSERT INTO events (type) VALUES ($1)", ["click"])
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        write_mode: str = "sync",
        timeout: float = 30.0,
        max_retries: int = 3,
        tag: Optional[str] = None,
    ):
        self._api_key = api_key or os.environ.get("DELTEX_API_KEY", "")
        if not self._api_key:
            raise DeltexError("No API key. Set DELTEX_API_KEY env var or pass api_key=")

        ep = (endpoint or os.environ.get("DELTEX_ENDPOINT") or "https://db.deltex.dev").rstrip("/")
        self._url = f"{ep}/v1/query"
        self._write_mode = write_mode
        self._timeout = timeout
        self._max_retries = max_retries

        self._headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "X-Write-Mode": write_mode,
        }
        if tag:
            self._headers["X-Query-Tag"] = tag

    # ── Core ──────────────────────────────────────────────────────────────────

    def query(self, sql: str, params: Sequence[Param] = ()) -> List[Row]:
        """Execute SQL, return all rows."""
        return _run_query(_bind(sql, params), self._url, self._headers, self._timeout, self._max_retries).rows

    def query_one(self, sql: str, params: Sequence[Param] = ()) -> Optional[Row]:
        """Execute SQL, return first row or None."""
        rows = _run_query(_bind(sql, params), self._url, self._headers, self._timeout, self._max_retries).rows
        return rows[0] if rows else None

    def execute(self, sql: str, params: Sequence[Param] = ()) -> int:
        """Execute a mutation, return rows affected."""
        return _run_query(_bind(sql, params), self._url, self._headers, self._timeout, self._max_retries).rows_affected

    def execute_raw(self, sql: str, params: Sequence[Param] = ()) -> QueryResult:
        """Execute SQL, return full QueryResult with commit_status and execution_ms."""
        return _run_query(_bind(sql, params), self._url, self._headers, self._timeout, self._max_retries)

    def transaction(self, fn):
        """
        Execute an atomic transaction via Deltex's /transaction endpoint.
        The tx client collects mutating SQL statements; they are sent atomically.
        Read queries inside fn() execute immediately (live reads).

        Example:
            def do_transfer(tx):
                tx.execute("UPDATE accounts SET balance = balance - $1 WHERE id = $2", [100, 1])
                tx.execute("UPDATE accounts SET balance = balance + $1 WHERE id = $2", [100, 2])
            db.transaction(do_transfer)
        """
        statements: list = []

        # Collecting proxy — mutations go to statements[], reads execute live
        class CollectingClient:
            def __init__(self_inner):
                pass
            def query(self_inner, sql: str, params=()) -> list:
                return self.query(sql, params)
            def query_one(self_inner, sql: str, params=()):
                return self.query_one(sql, params)
            def execute(self_inner, sql: str, params=()) -> int:
                statements.append(_bind(sql, params))
                return 0
            def execute_raw(self_inner, sql: str, params=()) -> "QueryResult":
                statements.append(_bind(sql, params))
                return QueryResult(rows=[], columns=[], rows_affected=0)

        tx = CollectingClient()
        user_result = fn(tx)

        if not statements:
            return user_result

        # Send to /transaction endpoint atomically
        tx_url = self._url.replace("/v1/query", "/v1/transaction")
        body = json.dumps({"statements": statements, "isolation": "SERIALIZABLE"}).encode()
        req = urllib.request.Request(tx_url, data=body, method="POST", headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
                if data.get("success") is False:
                    raise DeltexError(data.get("message", "Transaction failed"), 500, "; ".join(statements))
        except urllib.error.HTTPError as e:
            data = json.loads(e.read())
            raise DeltexError(data.get("message", str(e)), e.code, "; ".join(statements))

        return user_result

    def batch(self, statements: "list[str]") -> int:
        """Atomically execute an array of SQL statements in ONE round-trip.

        The fastest way to apply many writes: the engine coalesces them into a
        single durable KV commit, so N statements cost ~one write (O(1)) instead
        of N separate round-trips (~N x 300ms). Prefer this — or a single
        multi-row INSERT — over looping execute() for bulk work.

        Runs as a transaction (all-or-nothing). Returns total rows affected.
        """
        if not statements:
            return 0
        tx_url = self._url.replace("/v1/query", "/v1/transaction")
        body = json.dumps({"statements": statements, "isolation": "SERIALIZABLE"}).encode()
        req = urllib.request.Request(tx_url, data=body, method="POST", headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            data = json.loads(e.read())
            raise DeltexError(data.get("message", str(e)), e.code, "; ".join(statements))
        if data.get("success") is False:
            raise DeltexError(data.get("message", "Batch failed"), 500, "; ".join(statements))
        return int(data.get("affected_rows") or 0)

    # ── Deltex-specific ───────────────────────────────────────────────────────

    def with_write_mode(self, mode: str) -> "Client":
        """Return new client with given write mode (sync|async|edge)."""
        c = Client.__new__(Client)
        c._api_key = self._api_key
        c._url = self._url
        c._write_mode = mode
        c._timeout = self._timeout
        c._max_retries = self._max_retries
        c._headers = {**self._headers, "X-Write-Mode": mode}
        return c

    @property
    def strong(self) -> "Client":
        """Return a client that bypasses cache (X-Consistency: strong)."""
        c = Client.__new__(Client)
        c._api_key = self._api_key
        c._url = self._url
        c._write_mode = self._write_mode
        c._timeout = self._timeout
        c._max_retries = self._max_retries
        c._headers = {**self._headers, "X-Consistency": "strong"}
        return c

    def with_idempotency_key(self, key: str) -> "Client":
        """Return a client that sends X-Idempotency-Key for safe retries."""
        c = Client.__new__(Client)
        c._api_key = self._api_key
        c._url = self._url
        c._write_mode = self._write_mode
        c._timeout = self._timeout
        c._max_retries = self._max_retries
        c._headers = {**self._headers, "X-Idempotency-Key": key}
        return c

    def with_tag(self, tag: str) -> "Client":
        """Return a client that tags all queries with X-Query-Tag."""
        c = Client.__new__(Client)
        c._api_key = self._api_key
        c._url = self._url
        c._write_mode = self._write_mode
        c._timeout = self._timeout
        c._max_retries = self._max_retries
        c._headers = {**self._headers, "X-Query-Tag": tag}
        return c

    def __repr__(self) -> str:
        return f"deltex.Client(endpoint={self._url!r}, write_mode={self._write_mode!r})"


def connect(
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    write_mode: str = "sync",
    timeout: float = 30.0,
    max_retries: int = 3,
    tag: Optional[str] = None,
) -> Client:
    """
    Create a Deltex client.

    Args:
        api_key:     Bearer token (default: DELTEX_API_KEY env var)
        endpoint:    Engine URL (default: DELTEX_ENDPOINT or https://db.deltex.dev)
        write_mode:  "sync" | "edge" | "async"  (default: "sync", durable)
        timeout:     Request timeout in seconds (default: 30)
        max_retries: Auto-retry on 429 (default: 3)
        tag:         X-Query-Tag for all requests

    Example:
        db = deltex.connect()
        users = db.query("SELECT * FROM users WHERE active = $1", [True])
    """
    return Client(
        api_key=api_key,
        endpoint=endpoint,
        write_mode=write_mode,
        timeout=timeout,
        max_retries=max_retries,
        tag=tag,
    )
