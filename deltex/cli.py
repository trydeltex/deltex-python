#!/usr/bin/env python3
"""
deltex — Command-line client for Deltex edge-native SQL database.

Usage:
    deltex query "SELECT * FROM users LIMIT 10"
    deltex tables
    deltex schema users
    deltex exec "INSERT INTO events (type) VALUES ('click')"
    deltex bench --samples 20
    deltex health

Environment:
    DELTEX_API_KEY   Bearer token
    DELTEX_ENDPOINT  Engine URL (default: https://db.deltex.dev)
"""

from __future__ import annotations

import os
import sys
import json
import argparse
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


ENDPOINT = os.environ.get("DELTEX_ENDPOINT", "https://db.deltex.dev").rstrip("/")
API_KEY = os.environ.get("DELTEX_API_KEY", "")

ANSI_GREEN  = "\033[32m" if sys.stdout.isatty() else ""
ANSI_RED    = "\033[31m" if sys.stdout.isatty() else ""
ANSI_YELLOW = "\033[33m" if sys.stdout.isatty() else ""
ANSI_CYAN   = "\033[36m" if sys.stdout.isatty() else ""
ANSI_BOLD   = "\033[1m"  if sys.stdout.isatty() else ""
ANSI_DIM    = "\033[2m"  if sys.stdout.isatty() else ""
ANSI_RESET  = "\033[0m"  if sys.stdout.isatty() else ""


def sql(query: str, write_mode: str = "sync") -> Dict[str, Any]:
    if not API_KEY:
        print(f"{ANSI_RED}Error: DELTEX_API_KEY not set{ANSI_RESET}", file=sys.stderr)
        sys.exit(1)
    req = urllib.request.Request(
        f"{ENDPOINT}/v1/query",
        data=json.dumps({"sql": query}).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
            "X-Write-Mode": write_mode,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()), resp.headers
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.headers


def print_table(columns: List[str], rows: List[Dict]) -> None:
    if not columns:
        return
    # Compute column widths
    widths = {c: max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0)) for c in columns}
    sep = "+" + "+".join("-" * (widths[c] + 2) for c in columns) + "+"
    header = "|" + "|".join(f" {c:<{widths[c]}} " for c in columns) + "|"
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        line = "|" + "|".join(f" {str(row.get(c, '')):<{widths[c]}} " for c in columns) + "|"
        print(line)
    print(sep)
    print(f"{ANSI_DIM}{len(rows)} row(s){ANSI_RESET}")


def cmd_query(args: argparse.Namespace) -> None:
    """Execute SQL and display results."""
    t0 = time.perf_counter()
    result, headers = sql(args.sql, args.write_mode)
    elapsed = (time.perf_counter() - t0) * 1000

    if result.get("success") is False:
        msg = result.get("message", "Unknown error")
        print(f"{ANSI_RED}Error:{ANSI_RESET} {msg}", file=sys.stderr)
        sys.exit(1)

    cols = result.get("columns") or []
    rows = result.get("rows") or []

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        if cols and rows:
            print_table(cols, rows)
        elif cols:
            print_table(cols, [])
        else:
            affected = result.get("affected_rows", 0)
            print(f"{ANSI_GREEN}OK{ANSI_RESET} — {affected} row(s) affected")

    # Show timing
    commit = headers.get("x-commit-status") or headers.get("X-Commit-Status") or ""
    st = headers.get("server-timing") or ""
    server_ms = ""
    import re
    m = re.search(r"total;dur=([\d.]+)", st)
    if m:
        server_ms = f" server={float(m.group(1)):.1f}ms"
    if commit:
        print(f"{ANSI_DIM}[{commit}{server_ms} wall={elapsed:.0f}ms]{ANSI_RESET}")


def cmd_tables(args: argparse.Namespace) -> None:
    """List all tables."""
    result, _ = sql("SHOW TABLES")
    rows = result.get("rows") or []
    if not rows:
        print(f"{ANSI_DIM}(no tables){ANSI_RESET}")
        return
    cols = result.get("columns") or list(rows[0].keys())
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print_table(cols, rows)


def cmd_schema(args: argparse.Namespace) -> None:
    """Show table schema."""
    result, _ = sql(f"SHOW COLUMNS FROM {args.table}")
    rows = result.get("rows") or []
    if result.get("success") is False:
        print(f"{ANSI_RED}Error:{ANSI_RESET} {result.get('message', '')}", file=sys.stderr)
        sys.exit(1)
    cols = result.get("columns") or list(rows[0].keys()) if rows else []
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(f"{ANSI_BOLD}Table: {args.table}{ANSI_RESET}")
        print_table(cols, rows)


def cmd_exec(args: argparse.Namespace) -> None:
    """Execute a mutating SQL statement."""
    t0 = time.perf_counter()
    result, headers = sql(args.sql, args.write_mode)
    elapsed = (time.perf_counter() - t0) * 1000

    if result.get("success") is False:
        print(f"{ANSI_RED}Error:{ANSI_RESET} {result.get('message', '')}", file=sys.stderr)
        sys.exit(1)

    affected = result.get("affected_rows", 0)
    commit = headers.get("x-commit-status") or headers.get("X-Commit-Status") or ""
    commit_str = f" [{commit}]" if commit else ""
    print(f"{ANSI_GREEN}OK{ANSI_RESET} — {affected} row(s) affected{commit_str} ({elapsed:.0f}ms)")


def cmd_health(args: argparse.Namespace) -> None:
    """Show engine health status."""
    req = urllib.request.Request(f"{ENDPOINT}/health")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"{ANSI_RED}Error:{ANSI_RESET} {e}", file=sys.stderr)
        sys.exit(1)

    status = data.get("status", "?")
    pop = data.get("pop", "?")
    region = data.get("kv_region", "?")
    sandbox = "reusable" if data.get("reusable_sandbox") else "cold"
    color = ANSI_GREEN if status == "ok" else ANSI_RED

    print(f"{color}{status}{ANSI_RESET}  PoP={pop}  kv_region={region}  sandbox={sandbox}")
    if args.json:
        print(json.dumps(data, indent=2))


def cmd_bench(args: argparse.Namespace) -> None:
    """Run a quick latency benchmark."""
    if not API_KEY:
        print(f"{ANSI_RED}Error: DELTEX_API_KEY not set{ANSI_RESET}", file=sys.stderr)
        sys.exit(1)
    print(f"{ANSI_BOLD}Deltex Latency Benchmark{ANSI_RESET}")
    print(f"{ANSI_DIM}Endpoint: {ENDPOINT}  Samples: {args.samples}{ANSI_RESET}")
    print()

    # Create bench table
    sql("CREATE TABLE IF NOT EXISTS _cli_bench (id INT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")

    times = []
    for i in range(args.samples):
        time.sleep(0.35)  # Stay under rate limit
        t0 = time.perf_counter()
        result, headers = sql(
            f"INSERT INTO _cli_bench (id, ts) VALUES ({i % 1000}, NOW()) ON CONFLICT (id) DO UPDATE SET ts = NOW()"
        )
        elapsed = (time.perf_counter() - t0) * 1000

        st = headers.get("server-timing") or ""
        import re
        m = re.search(r"total;dur=([\d.]+)", st)
        server_ms = float(m.group(1)) if m else 0
        commit = headers.get("x-commit-status") or ""
        times.append(server_ms if server_ms > 0 else elapsed)

        color = ANSI_GREEN if elapsed < 100 else ANSI_YELLOW
        print(f"  [{i+1:2d}/{args.samples}] {color}{elapsed:6.1f}ms{ANSI_RESET}  server={server_ms:.1f}ms  [{commit}]")

    if times:
        times.sort()
        n = len(times)
        p50 = times[int(n * 0.5)]
        p95 = times[int(n * 0.95)]
        p99 = times[min(int(n * 0.99), n-1)]
        print(f"\n{ANSI_BOLD}Results:{ANSI_RESET} p50={p50:.1f}ms  p95={p95:.1f}ms  p99={p99:.1f}ms  min={min(times):.1f}ms  max={max(times):.1f}ms")


def cmd_migrate(args: argparse.Namespace) -> None:
    """Apply SQL migration files in order."""
    import hashlib

    if not API_KEY:
        print(f"{ANSI_RED}Error: DELTEX_API_KEY not set{ANSI_RESET}", file=sys.stderr)
        sys.exit(1)

    # Ensure _migrations tracking table exists (unless dry-run)
    if not args.dry_run:
        sql_request("CREATE TABLE IF NOT EXISTS _migrations (id INTEGER PRIMARY KEY, filename TEXT NOT NULL, checksum TEXT, applied_at TEXT)", "sync")

    total_stmts = 0
    for filepath in args.files:
        try:
            with open(filepath, "r") as f:
                content = f.read()
        except FileNotFoundError:
            print(f"{ANSI_RED}File not found: {filepath}{ANSI_RESET}")
            sys.exit(1)

        # Compute checksum
        checksum = hashlib.md5(content.encode()).hexdigest()[:16]
        filename = os.path.basename(filepath)

        # Check if already applied
        if not args.dry_run:
            existing = sql_request(f"SELECT id FROM _migrations WHERE filename = '{filename}'")
            if existing.get("rows"):
                print(f"  {ANSI_YELLOW}⚠ Skipping {filename} (already applied){ANSI_RESET}")
                continue

        # Split on semicolons (skip blank lines; remove leading comment lines per statement)
        raw_stmts = content.split(";")
        stmts = []
        for s in raw_stmts:
            cleaned = "\n".join(line for line in s.strip().splitlines() if not line.strip().startswith("--")).strip()
            if cleaned:
                stmts.append(cleaned)
        print(f"\n{ANSI_BOLD}[{filepath}]{ANSI_RESET} — {len(stmts)} statement(s)  {ANSI_DIM}checksum={checksum}{ANSI_RESET}")

        for i, stmt in enumerate(stmts, 1):
            if args.dry_run:
                print(f"  {ANSI_DIM}[dry-run]{ANSI_RESET} {stmt[:80]}")
                continue
            result = sql_request(stmt, args.write_mode)
            status_icon = f"{ANSI_GREEN}✓{ANSI_RESET}" if result.get("success") else f"{ANSI_RED}✗{ANSI_RESET}"
            msg = result.get("message", "")[:60]
            print(f"  {status_icon} [{i}/{len(stmts)}] {stmt[:60]!r:<65} {ANSI_DIM}{msg}{ANSI_RESET}")
            if not result.get("success"):
                print(f"\n{ANSI_RED}Migration failed at statement {i}:{ANSI_RESET}")
                print(f"  SQL: {stmt}")
                print(f"  Error: {result.get('message', '')}")
                sys.exit(1)
            total_stmts += 1

        # Record migration in _migrations table
        if not args.dry_run:
            next_id_r = sql_request("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM _migrations")
            next_id = (next_id_r.get("rows") or [{}])[0].get("nid", 1)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            sql_request(f"INSERT INTO _migrations (id, filename, checksum, applied_at) VALUES ({next_id}, '{filename}', '{checksum}', '{now}')", "sync")
            print(f"  {ANSI_DIM}Recorded in _migrations (id={next_id}){ANSI_RESET}")

    if not args.dry_run:
        print(f"\n{ANSI_GREEN}✓ Migration complete:{ANSI_RESET} {total_stmts} statement(s) applied from {len(args.files)} file(s)")
    else:
        print(f"\n{ANSI_YELLOW}Dry run complete — no changes made{ANSI_RESET}")


def cmd_backup(args: argparse.Namespace) -> None:
    """Export all tables to a SQL backup file."""
    if not API_KEY:
        print(f"{ANSI_RED}Error: DELTEX_API_KEY not set{ANSI_RESET}", file=sys.stderr)
        sys.exit(1)

    # Get table list
    r = sql_request("SHOW TABLES")
    if not r.get("success"):
        print(f"{ANSI_RED}Failed to list tables: {r.get('message', '')}{ANSI_RESET}")
        sys.exit(1)

    all_tables = [row.get("table_name", "") for row in (r.get("rows") or [])]
    tables = args.tables if args.tables else all_tables

    lines = [
        "-- Deltex database backup",
        f"-- Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        f"-- Tables: {', '.join(tables)}",
        "",
    ]

    for table in tables:
        if not table:
            continue
        print(f"  Backing up {table}...", end=" ", flush=True)

        # Get CREATE TABLE statement
        cr = sql_request(f"SHOW CREATE TABLE {table}")
        if cr.get("success") and cr.get("rows"):
            create_sql = cr["rows"][0].get("Create Table") or cr["rows"][0].get("sql", "")
            if create_sql:
                lines.append(f"-- Table: {table}")
                lines.append(f"DROP TABLE IF EXISTS {table};")
                lines.append(f"{create_sql.rstrip(';')};")
                lines.append("")

        # Get data
        dr = sql_request(f"SELECT * FROM {table}")
        if dr.get("success") and dr.get("rows"):
            rows = dr["rows"]
            cols = dr.get("columns") or (list(rows[0].keys()) if rows else [])
            cols = [c for c in cols if c != "_id"]

            for row in rows:
                vals = []
                for col in cols:
                    v = row.get(col)
                    if v is None:
                        vals.append("NULL")
                    elif isinstance(v, bool):
                        vals.append("TRUE" if v else "FALSE")
                    elif isinstance(v, (int, float)):
                        vals.append(str(v))
                    else:
                        escaped = str(v).replace("'", "''")
                        vals.append(f"'{escaped}'")
                col_list = ", ".join(cols)
                val_list = ", ".join(vals)
                lines.append(f"INSERT INTO {table} ({col_list}) VALUES ({val_list});")
            print(f"{ANSI_GREEN}{len(rows)} rows{ANSI_RESET}")
        else:
            print(f"{ANSI_YELLOW}0 rows{ANSI_RESET}")
        lines.append("")

    output = "\n".join(lines)
    with open(args.output, "w") as f:
        f.write(output)
    print(f"\n{ANSI_GREEN}✓ Backup written to {args.output}{ANSI_RESET} ({len(output)} bytes, {len(tables)} tables)")


def cmd_signup(args: argparse.Namespace) -> None:
    """Create a new Deltex account via self-service signup."""
    ctrl_url = os.environ.get("DELTEX_CTRL_URL", "https://ctrl.deltex.dev")
    email = args.email
    org_name = args.org or email.split("@")[0]
    db_name = args.db

    payload = json.dumps({"email": email, "org_name": org_name, "db_name": db_name}).encode()
    req = urllib.request.Request(
        f"{ctrl_url}/v1/signup",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            resp = json.loads(e.read())
        except Exception:
            resp = {"success": False, "message": str(e)}

    if resp.get("success"):
        print(f"\n{ANSI_GREEN}✓ Account created!{ANSI_RESET}")
        print(f"\n  {ANSI_BOLD}API Key:{ANSI_RESET}    {resp.get('api_key')}")
        print(f"  {ANSI_BOLD}Database:{ANSI_RESET}   {resp.get('db_name')} (id: {resp.get('db_id')})")
        print(f"  {ANSI_BOLD}Org ID:{ANSI_RESET}     {resp.get('org_id')}")
        print(f"  {ANSI_BOLD}Endpoint:{ANSI_RESET}   {resp.get('endpoint', 'https://db.deltex.dev')}")
        print(f"\n  {ANSI_YELLOW}⚠ Save your API key — it cannot be retrieved again{ANSI_RESET}")
        print(f"\n  Quick start:")
        print(f"  export DELTEX_API_KEY={resp.get('api_key')}")
        print(f"  deltex query 'SELECT 1 AS hello'")
    elif resp.get("error") == "EMAIL_EXISTS":
        print(f"\n{ANSI_YELLOW}Account already exists for {email}{ANSI_RESET}")
        print(f"  Org ID: {resp.get('org_id')}")
        print(f"  Use your existing API key or create a new key via the dashboard.")
    else:
        print(f"\n{ANSI_RED}Signup failed: {resp.get('message', 'Unknown error')}{ANSI_RESET}")
        sys.exit(1)


def sql_request(stmt: str, write_mode: str = "sync") -> Dict[str, Any]:
    """Internal helper for CLI SQL execution."""
    req = urllib.request.Request(
        f"{ENDPOINT}/v1/query",
        data=json.dumps({"sql": stmt}).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
            "X-Write-Mode": write_mode,
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", 5)))
                continue
            return json.loads(e.read())
        except Exception as ex:
            return {"success": False, "message": str(ex)}
    return {"success": False, "message": "max retries exceeded"}


def cmd_restore(args: argparse.Namespace) -> None:
    """Restore database from a SQL backup file."""
    if not API_KEY:
        print(f"{ANSI_RED}Error: DELTEX_API_KEY not set{ANSI_RESET}", file=sys.stderr); sys.exit(1)

    filepath = args.file
    try:
        with open(filepath, "r") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"{ANSI_RED}File not found: {filepath}{ANSI_RESET}"); sys.exit(1)

    # Parse statements (skip comments, split on semicolons)
    stmts = []
    for s in content.split(";"):
        stripped = "\n".join(line for line in s.strip().splitlines() if not line.strip().startswith("--")).strip()
        if stripped:
            stmts.append(stripped)

    print(f"{ANSI_BOLD}Restoring from {filepath}{ANSI_RESET} — {len(stmts)} statements")
    if not args.force:
        confirm = input("This will execute all statements against your database. Continue? [y/N] ")
        if confirm.lower() != "y":
            print("Aborted."); sys.exit(0)

    errors = 0
    for i, stmt in enumerate(stmts, 1):
        r = sql_request(stmt, args.write_mode)
        if r.get("success"):
            if args.verbose:
                print(f"  {ANSI_GREEN}✓{ANSI_RESET} [{i}/{len(stmts)}] {stmt[:60]!r}")
        else:
            errors += 1
            print(f"  {ANSI_RED}✗{ANSI_RESET} [{i}/{len(stmts)}] {stmt[:60]!r}")
            print(f"    Error: {r.get('message', '')}")
            if not args.continue_on_error:
                print(f"\n{ANSI_RED}Restore aborted at statement {i}{ANSI_RESET}")
                sys.exit(1)
        if not args.verbose and i % 10 == 0:
            print(f"  {i}/{len(stmts)} statements applied...", end="\r")

    if errors == 0:
        print(f"\n{ANSI_GREEN}✓ Restore complete:{ANSI_RESET} {len(stmts)} statements applied")
    else:
        print(f"\n{ANSI_YELLOW}Restore done with {errors} error(s){ANSI_RESET}")


def cmd_keys(args: argparse.Namespace) -> None:
    """Manage API keys via the control plane."""
    ctrl_url = os.environ.get("DELTEX_CTRL_URL", "https://ctrl.deltex.dev")

    def ctrl_req(method, path, body=None):
        req = urllib.request.Request(
            f"{ctrl_url}{path}",
            data=json.dumps(body).encode() if body else None,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try: return json.loads(e.read())
            except Exception: return {"success": False, "message": str(e)}

    if args.keys_cmd == "list":
        r = ctrl_req("GET", "/v1/apikeys")
        keys = r.get("api_keys", r.get("keys", []))
        if not keys:
            print("No API keys found.")
            return
        print(f"\n{'Key ID':<20} {'DB':<20} {'Permissions':<20} {'Created'}")
        print("-" * 80)
        for k in keys:
            print(f"  {k.get('key_id','')[:18]:<20} {k.get('db_name',''):<20} {','.join(k.get('permissions',[])):<20} {k.get('created_at','')[:10]}")
        print(f"\n{len(keys)} key(s)")

    elif args.keys_cmd == "create":
        perms = args.permissions.split(",") if args.permissions else ["read", "write"]
        r = ctrl_req("POST", "/v1/apikeys", {"permissions": perms})
        if r.get("success"):
            print(f"\n{ANSI_GREEN}✓ API key created:{ANSI_RESET}")
            print(f"  Key: {r.get('api_key')}")
            print(f"  ID:  {r.get('key_id')}")
            print(f"  {ANSI_YELLOW}Save this key — it cannot be retrieved again{ANSI_RESET}")
        else:
            print(f"{ANSI_RED}Failed: {r.get('message', '')}{ANSI_RESET}")

    elif args.keys_cmd == "revoke":
        r = ctrl_req("DELETE", f"/v1/apikeys/{args.key_id}")
        if r.get("success"):
            print(f"{ANSI_GREEN}✓ Key {args.key_id} revoked{ANSI_RESET}")
        else:
            print(f"{ANSI_RED}Failed: {r.get('message', '')}{ANSI_RESET}")

    elif args.keys_cmd == "rotate":
        # Rotate = create new key + revoke old one
        perms = args.permissions.split(",") if hasattr(args, 'permissions') and args.permissions else ["read", "write", "admin"]
        r = ctrl_req("POST", "/v1/apikeys", {"permissions": perms})
        if r.get("success"):
            new_key = r.get("api_key")
            new_id = r.get("key_id")
            print(f"{ANSI_GREEN}✓ New key created: {new_key}{ANSI_RESET}")
            if args.revoke_old:
                r2 = ctrl_req("DELETE", f"/v1/apikeys/{args.revoke_old}")
                if r2.get("success"):
                    print(f"{ANSI_GREEN}✓ Old key {args.revoke_old} revoked{ANSI_RESET}")
                else:
                    print(f"{ANSI_YELLOW}Warning: could not revoke old key: {r2.get('message', '')}{ANSI_RESET}")
        else:
            print(f"{ANSI_RED}Failed: {r.get('message', '')}{ANSI_RESET}")


def cmd_migrate_status(args: argparse.Namespace) -> None:
    """Show migration history from _migrations tracking table."""
    if not API_KEY:
        print(f"{ANSI_RED}Error: DELTEX_API_KEY not set{ANSI_RESET}", file=sys.stderr); sys.exit(1)

    # Ensure _migrations table exists
    sql_request("CREATE TABLE IF NOT EXISTS _migrations (id INTEGER PRIMARY KEY, filename TEXT, checksum TEXT, applied_at TEXT)", "sync")

    r = sql_request("SELECT id, filename, checksum, applied_at FROM _migrations ORDER BY id")
    rows = r.get("rows", [])
    if not rows:
        print("No migrations applied yet.")
        return
    print(f"\n{'ID':<5} {'File':<40} {'Applied':<20} {'Checksum'}")
    print("-" * 80)
    for row in rows:
        print(f"  {str(row.get('id','')):<5} {row.get('filename','')[:38]:<40} {row.get('applied_at','')[:19]:<20} {row.get('checksum','')[:8]}")
    print(f"\n{len(rows)} migration(s) applied")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="deltex",
        description="Deltex edge-native SQL database CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables:
  DELTEX_API_KEY    Bearer token (required)
  DELTEX_ENDPOINT   Engine URL (default: https://db.deltex.dev)

Examples:
  deltex query "SELECT * FROM users LIMIT 10"
  deltex query "SELECT * FROM users WHERE id = 1" --json
  deltex tables
  deltex schema orders
  deltex exec "DELETE FROM sessions WHERE expires_at < NOW()"
  deltex health
  deltex bench --samples 30
""",
    )
    parser.add_argument("--endpoint", default=None, help="Override DELTEX_ENDPOINT")
    parser.add_argument("--api-key", default=None, help="Override DELTEX_API_KEY")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # query
    p_query = subparsers.add_parser("query", help="Execute SQL and show results")
    p_query.add_argument("sql", help="SQL statement")
    p_query.add_argument("--json", action="store_true", help="Output JSON")
    p_query.add_argument("--write-mode", default="sync", choices=["sync", "async", "edge"])
    p_query.set_defaults(func=cmd_query)

    # tables
    p_tables = subparsers.add_parser("tables", help="List all tables")
    p_tables.add_argument("--json", action="store_true", help="Output JSON")
    p_tables.set_defaults(func=cmd_tables)

    # schema
    p_schema = subparsers.add_parser("schema", help="Show table schema")
    p_schema.add_argument("table", help="Table name")
    p_schema.add_argument("--json", action="store_true", help="Output JSON")
    p_schema.set_defaults(func=cmd_schema)

    # exec
    p_exec = subparsers.add_parser("exec", help="Execute a mutating SQL statement")
    p_exec.add_argument("sql", help="SQL statement")
    p_exec.add_argument("--write-mode", default="sync", choices=["sync", "async", "edge"])
    p_exec.set_defaults(func=cmd_exec)

    # health
    p_health = subparsers.add_parser("health", help="Show engine health status")
    p_health.add_argument("--json", action="store_true", help="Output JSON")
    p_health.set_defaults(func=cmd_health)

    # bench
    p_bench = subparsers.add_parser("bench", help="Run a quick latency benchmark")
    p_bench.add_argument("--samples", type=int, default=20, help="Number of requests")
    p_bench.set_defaults(func=cmd_bench)

    # migrate
    p_migrate = subparsers.add_parser("migrate", help="Apply SQL migration files")
    p_migrate.add_argument("files", nargs="+", help="SQL migration files to apply (in order)")
    p_migrate.add_argument("--dry-run", action="store_true", help="Print SQL without executing")
    p_migrate.add_argument("--write-mode", default="sync", choices=["sync", "async", "edge"])
    p_migrate.set_defaults(func=cmd_migrate)

    # backup
    p_backup = subparsers.add_parser("backup", help="Export all tables to SQL file")
    p_backup.add_argument("--output", "-o", default="backup.sql", help="Output file (default: backup.sql)")
    p_backup.add_argument("--tables", nargs="*", help="Specific tables to backup (default: all)")
    p_backup.set_defaults(func=cmd_backup)

    # signup
    p_signup = subparsers.add_parser("signup", help="Create a new Deltex account")
    p_signup.add_argument("email", help="Email address")
    p_signup.add_argument("--org", default=None, help="Organization name (default: email prefix)")
    p_signup.add_argument("--db", default="default", help="Initial database name (default: default)")
    p_signup.set_defaults(func=cmd_signup)

    # restore
    p_restore = subparsers.add_parser("restore", help="Restore database from a SQL backup file")
    p_restore.add_argument("file", help="Backup SQL file to restore from")
    p_restore.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    p_restore.add_argument("--continue-on-error", action="store_true", help="Don't stop on errors")
    p_restore.add_argument("--verbose", "-v", action="store_true", help="Show each statement")
    p_restore.add_argument("--write-mode", default="sync", choices=["sync", "async", "edge"])
    p_restore.set_defaults(func=cmd_restore)

    # keys
    p_keys = subparsers.add_parser("keys", help="Manage API keys")
    keys_sub = p_keys.add_subparsers(dest="keys_cmd", required=True)
    keys_sub.add_parser("list", help="List all API keys")
    p_keys_create = keys_sub.add_parser("create", help="Create a new API key")
    p_keys_create.add_argument("--permissions", default="read,write", help="Comma-separated permissions")
    p_keys_revoke = keys_sub.add_parser("revoke", help="Revoke an API key")
    p_keys_revoke.add_argument("key_id", help="Key ID to revoke")
    p_keys_rotate = keys_sub.add_parser("rotate", help="Create new key and optionally revoke old one")
    p_keys_rotate.add_argument("--revoke-old", metavar="KEY_ID", help="Key ID to revoke after creating new one")
    p_keys_rotate.add_argument("--permissions", default="read,write,admin")
    p_keys.set_defaults(func=cmd_keys)

    # migrate status
    p_mstatus = subparsers.add_parser("migrations", help="Show migration history")
    p_mstatus.set_defaults(func=cmd_migrate_status)

    args = parser.parse_args()

    # Apply global overrides
    global ENDPOINT, API_KEY
    if args.endpoint:
        ENDPOINT = args.endpoint.rstrip("/")
    if args.api_key:
        API_KEY = args.api_key

    args.func(args)


if __name__ == "__main__":
    main()
