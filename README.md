# deltex — Python client

Official Python client for [Deltex](https://deltex.dev) — edge-native SQL database.

## Install

```bash
pip install deltex
```

## Quick start

```python
import deltex

# Auto-reads DELTEX_API_KEY from environment
db = deltex.connect()

# Query
users = db.query("SELECT * FROM users WHERE active = $1", [True])
user  = db.query_one("SELECT * FROM users WHERE id = $1", [42])

# Mutation
n = db.execute("INSERT INTO events (type, ts) VALUES ($1, NOW())", ["pageview"])

# Full result with commit status
result = db.execute_raw("INSERT INTO orders (amount) VALUES ($1)", [99.99])
print(result.commit_status)  # "edge-accepted" | "committed"
print(result.execution_ms)   # server-side execution time
```

## API

### `deltex.connect(api_key=None, endpoint=None, write_mode="sync", ...)`

| Param | Default | Description |
|-------|---------|-------------|
| `api_key` | `DELTEX_API_KEY` env | Bearer token |
| `endpoint` | `DELTEX_ENDPOINT` or `https://db.deltex.dev` | Engine URL |
| `write_mode` | `"sync"` | `"sync"` / `"edge"` / `"async"` |
| `timeout` | `30.0` | Request timeout (seconds) |
| `max_retries` | `3` | Auto-retry on 429 |
| `tag` | `None` | X-Query-Tag for analytics |

### Methods

```python
db.query(sql, params=[])       → list[dict]
db.query_one(sql, params=[])   → dict | None
db.execute(sql, params=[])     → int  (rows affected)
db.execute_raw(sql, params=[]) → QueryResult

db.transaction(fn)             # BEGIN → fn(tx) → COMMIT
db.batch(statements)           # atomic multi-statement, one round-trip → int

db.with_write_mode("sync")     → Client  (per-client write mode)
db.strong                      → Client  (X-Consistency: strong)
db.with_idempotency_key(key)   → Client  (safe retry deduplication)
db.with_tag(tag)               → Client  (X-Query-Tag)
```

## Async

```python
import asyncio
import deltex

async def main():
    async with deltex.async_connect() as db:
        users = await db.query("SELECT * FROM users LIMIT 10")
        print(users)

asyncio.run(main())
```

## Transaction

```python
def transfer(tx):
    tx.execute("UPDATE accounts SET balance = balance - $1 WHERE id = $2", [100, 1])
    tx.execute("UPDATE accounts SET balance = balance + $1 WHERE id = $2", [100, 2])

db.transaction(transfer)
```

## Batch — fastest bulk write

`batch()` applies a list of SQL statements in **one round-trip**, committed
atomically, and returns total rows affected:

```python
n = db.batch([
    "INSERT INTO products (name, price) VALUES ('Apple', 0.99)",
    "INSERT INTO products (name, price) VALUES ('Banana', 0.59)",
])  # n == 2
```

Looping `execute` makes one durable commit per statement. `batch()` (and a
single multi-row `INSERT`) coalesce them into one commit — O(1) instead of O(N)
— so it's far faster for bulk writes. `batch()` takes raw SQL (no binding) — for
untrusted values, build statements safely or use `transaction`.

## CLI

```bash
export DELTEX_API_KEY="dtx_k_..."

deltex query "SELECT * FROM users LIMIT 5"
deltex tables
deltex schema orders
deltex exec "DELETE FROM sessions WHERE expires_at < NOW()"
deltex health
deltex bench --samples 30
```

## Write Modes

| Mode | Use when |
|------|----------|
| `sync` (default) | Everything by default; durable, never loses an acked write |
| `edge` | Caches, sessions, idempotent upserts — eventual durability |
| `async` | High-volume telemetry, fire-and-forget |

## Error handling

```python
from deltex import DeltexError, RateLimitError

try:
    db.query("SELECT * FROM nonexistent")
except RateLimitError as e:
    print(f"Rate limited, retry after {e.retry_after}s")
except DeltexError as e:
    print(f"Error {e.status}: {e.engine_message}")
    print(f"SQL: {e.sql}")
```

## License

MIT

---

## Common Patterns

### Error handling

```python
import time
from deltex import connect, RateLimitError, DeltexError

db = connect()
try:
    result = db.query("SELECT * FROM users WHERE id = $1", [42])
except RateLimitError as e:
    time.sleep(e.retry_after)
    result = db.query("SELECT * FROM users WHERE id = $1", [42])
except DeltexError as e:
    print(f"Query failed: {e}")
    raise
```

### Async client (non-blocking)

```python
import deltex

async def main():
    db = deltex.async_connect()
    rows = await db.query("SELECT * FROM products WHERE price < $1", [50.0])
    for row in rows:
        print(row["name"], row["price"])
```

### CLI usage

```bash
# Run a query
deltex query "SELECT COUNT(*) FROM users"

# Migrate
deltex migrate --file migrations/001_create_users.sql

# Backup
deltex backup --output backup.json

# Manage API keys
deltex keys list
deltex keys create --name "production"
deltex keys revoke --id key_id_here
```

## SDK Version

`v1.3.2` — see [CHANGELOG.md](../../CHANGELOG.md) for history.
