"""
Deltex asyncio client.
"""

from __future__ import annotations

import os
import asyncio
from typing import Any, Dict, List, Optional, Sequence

from .client import (
    _bind, _format_param, _TIMING_RE, _COMMIT_STATUS_VALUES,
    DeltexError, RateLimitError, QueryResult, Row, Param,
)


async def _run_query_async(
    sql: str,
    url: str,
    headers: Dict[str, str],
    timeout: float,
    max_retries: int,
) -> QueryResult:
    """Async HTTP execution using urllib in a thread (no external deps)."""
    from .client import _run_query
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: _run_query(sql, url, headers, timeout, max_retries),
    )


class AsyncClient:
    """
    Deltex asyncio SQL client.

    Example:
        async with deltex.async_connect() as db:
            users = await db.query("SELECT * FROM users WHERE active = $1", [True])
            user  = await db.query_one("SELECT * FROM users WHERE id = $1", [42])
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        write_mode: str = "edge",
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

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def query(self, sql: str, params: Sequence[Param] = ()) -> List[Row]:
        """Execute SQL, return all rows."""
        result = await _run_query_async(_bind(sql, params), self._url, self._headers, self._timeout, self._max_retries)
        return result.rows

    async def query_one(self, sql: str, params: Sequence[Param] = ()) -> Optional[Row]:
        """Execute SQL, return first row or None."""
        rows = (await _run_query_async(_bind(sql, params), self._url, self._headers, self._timeout, self._max_retries)).rows
        return rows[0] if rows else None

    async def execute(self, sql: str, params: Sequence[Param] = ()) -> int:
        """Execute a mutation, return rows affected."""
        return (await _run_query_async(_bind(sql, params), self._url, self._headers, self._timeout, self._max_retries)).rows_affected

    async def execute_raw(self, sql: str, params: Sequence[Param] = ()) -> QueryResult:
        """Execute SQL, return full QueryResult."""
        return await _run_query_async(_bind(sql, params), self._url, self._headers, self._timeout, self._max_retries)

    async def transaction(self, fn):
        """
        Execute an async transaction.

        Example:
            async def do_transfer(tx):
                await tx.execute("UPDATE accounts SET balance = balance - $1 WHERE id = $2", [100, 1])
                await tx.execute("UPDATE accounts SET balance = balance + $1 WHERE id = $2", [100, 2])

            await db.transaction(do_transfer)
        """
        sync_client = self.with_write_mode("sync")
        await sync_client.execute("BEGIN TRANSACTION")
        try:
            result = await fn(self)
            await sync_client.execute("COMMIT")
            return result
        except Exception:
            try:
                await sync_client.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def with_write_mode(self, mode: str) -> "AsyncClient":
        c = AsyncClient.__new__(AsyncClient)
        c._api_key = self._api_key
        c._url = self._url
        c._write_mode = mode
        c._timeout = self._timeout
        c._max_retries = self._max_retries
        c._headers = {**self._headers, "X-Write-Mode": mode}
        return c

    @property
    def strong(self) -> "AsyncClient":
        c = AsyncClient.__new__(AsyncClient)
        c._api_key = self._api_key
        c._url = self._url
        c._write_mode = self._write_mode
        c._timeout = self._timeout
        c._max_retries = self._max_retries
        c._headers = {**self._headers, "X-Consistency": "strong"}
        return c

    def with_tag(self, tag: str) -> "AsyncClient":
        c = AsyncClient.__new__(AsyncClient)
        c._api_key = self._api_key
        c._url = self._url
        c._write_mode = self._write_mode
        c._timeout = self._timeout
        c._max_retries = self._max_retries
        c._headers = {**self._headers, "X-Query-Tag": tag}
        return c

    def with_idempotency_key(self, key: str) -> "AsyncClient":
        c = AsyncClient.__new__(AsyncClient)
        c._api_key = self._api_key
        c._url = self._url
        c._write_mode = self._write_mode
        c._timeout = self._timeout
        c._max_retries = self._max_retries
        c._headers = {**self._headers, "X-Idempotency-Key": key}
        return c


def async_connect(
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    write_mode: str = "edge",
    timeout: float = 30.0,
    max_retries: int = 3,
    tag: Optional[str] = None,
) -> AsyncClient:
    """
    Create an async Deltex client.

    Example:
        async with deltex.async_connect() as db:
            users = await db.query("SELECT * FROM users")
    """
    return AsyncClient(
        api_key=api_key, endpoint=endpoint, write_mode=write_mode,
        timeout=timeout, max_retries=max_retries, tag=tag,
    )
