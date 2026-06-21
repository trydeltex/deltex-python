"""
deltex — Official Python client for Deltex edge-native SQL database.

Usage:
    import deltex

    db = deltex.connect()  # reads DELTEX_API_KEY from env

    # Query
    users = db.query("SELECT * FROM users WHERE active = $1", [True])
    user  = db.query_one("SELECT * FROM users WHERE id = $1", [42])

    # Execute (INSERT/UPDATE/DELETE)
    n = db.execute("INSERT INTO events (type, ts) VALUES ($1, NOW())", ["click"])

    # Async
    import asyncio
    async def main():
        async with deltex.async_connect() as db:
            users = await db.query("SELECT * FROM users")
"""

from .client import connect, Client, DeltexError, RateLimitError, QueryResult
from .async_client import async_connect, AsyncClient

__version__ = "1.3.3"
__all__ = [
    "connect",
    "async_connect",
    "Client",
    "AsyncClient",
    "DeltexError",
    "RateLimitError",
    "QueryResult",
]
