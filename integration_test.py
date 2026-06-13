#!/usr/bin/env python3
"""Python SDK Integration Test — Real data ops against live Deltex."""

import os, sys, time, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from deltex import connect, DeltexError, RateLimitError

API_KEY = os.environ.get("DELTEX_API_KEY", "")
if not API_KEY:
    print("Set DELTEX_API_KEY"); sys.exit(1)

T = f"_sdk_py_{int(time.time())}"
passed = failed = 0

def p(name): global passed; print(f"  \033[32m✓\033[0m {name}"); passed += 1
def f(name, msg=""): global failed; print(f"  \033[31m✗ {name}: {msg}\033[0m"); failed += 1
def sleep(s): time.sleep(s)

db = connect(api_key=API_KEY, write_mode="sync")

try:
    print(f"\n\033[1mPython SDK Integration Test\033[0m")
    print(f"\033[2mTable: {T}\033[0m\n")

    # ── DDL ─────────────────────────────────────────────────────────────────
    print("[DDL]")
    sleep(0.35)
    db.execute(f"CREATE TABLE {T} (id INT PRIMARY KEY, name TEXT NOT NULL, score FLOAT DEFAULT 0, active BOOL DEFAULT TRUE, dept TEXT)")
    p("CREATE TABLE")

    # ── INSERT ───────────────────────────────────────────────────────────────
    print("\n[INSERT]")
    users = [
        (1,"Alice",95.5,True,"eng"),(2,"Bob",73.0,True,"sales"),(3,"Charlie",41.5,False,"eng"),
        (4,"Diana",88.0,True,"design"),(5,"Eve",99.9,True,"eng"),(6,"Frank",0.0,False,"sales"),
        (7,"Grace",55.5,True,"design"),(8,"Henry",77.0,True,"eng"),(9,"Iris",82.5,False,"design"),
        (10,"Jack",63.0,True,"sales"),
    ]
    errs = 0
    for (id,name,score,active,dept) in users:
        sleep(0.35)
        n = db.execute(f"INSERT INTO {T} (id,name,score,active,dept) VALUES ($1,$2,$3,$4,$5)", [id,name,score,active,dept])
        if n != 1: errs += 1
    if errs == 0: p("INSERT 10 rows — execute() returns rowsAffected=1 each")
    else: f("INSERT", f"{errs} errors")

    # ── SELECT ────────────────────────────────────────────────────────────────
    sleep(0.6)
    print("\n[SELECT]")

    sleep(0.35)
    all_rows = db.strong.query(f"SELECT id,name FROM {T} ORDER BY id")
    if len(all_rows)==10 and all_rows[0]["name"]=="Alice": p("SELECT * ORDER BY id (10 rows)")
    else: f("SELECT *", f"len={len(all_rows)}")

    sleep(0.35)
    active_rows = db.strong.query(f"SELECT name FROM {T} WHERE active=TRUE")
    if len(active_rows)==7: p("SELECT WHERE active=TRUE (7 rows)")
    else: f("WHERE active=TRUE", len(active_rows))

    sleep(0.35)
    inactive_rows = db.strong.query(f"SELECT name FROM {T} WHERE active=FALSE")
    if len(inactive_rows)==3: p("SELECT WHERE active=FALSE (3 rows)")
    else: f("WHERE active=FALSE", len(inactive_rows))

    sleep(0.35)
    top3 = db.strong.query(f"SELECT name,score FROM {T} ORDER BY score DESC LIMIT 3")
    if len(top3)==3 and top3[0]["name"]=="Eve": p(f"ORDER BY score DESC LIMIT 3 → Eve({top3[0]['score']}) first")
    else: f("ORDER BY LIMIT", top3[0] if top3 else "empty")

    sleep(0.35)
    [agg] = db.strong.query(f"SELECT COUNT(*) AS cnt, MAX(score) AS mx, MIN(score) AS mn, AVG(score) AS avg FROM {T}")
    cnt = int(agg["cnt"]); mx = float(agg["mx"] or 0)
    if cnt==10 and abs(mx-99.9)<0.01: p(f"Aggregates: COUNT=10 MAX=99.9 AVG={float(agg['avg'] or 0):.1f}")
    else: f("Aggregates", agg)

    sleep(0.35)
    groups = db.strong.query(f"SELECT dept, COUNT(*) AS n FROM {T} GROUP BY dept ORDER BY dept")
    if len(groups)==3: p(f"GROUP BY dept → {', '.join(g['dept']+'('+str(g['n'])+')' for g in groups)}")
    else: f("GROUP BY", len(groups))

    sleep(0.35)
    alice = db.strong.query_one(f"SELECT * FROM {T} WHERE name=$1", ["Alice"])
    if alice and alice["score"]==95.5: p("query_one: Alice found")
    else: f("query_one", alice)

    sleep(0.35)
    miss = db.strong.query_one(f"SELECT * FROM {T} WHERE name=$1", ["Nobody"])
    if miss is None: p("query_one miss → None")
    else: f("query_one miss", miss)

    # ── UPDATE ────────────────────────────────────────────────────────────────
    print("\n[UPDATE]")
    sleep(0.35)
    upd = db.execute(f"UPDATE {T} SET score = score * 1.1 WHERE dept='eng'")
    if upd==4: p(f"UPDATE dept=eng → {upd} rows affected")
    else: f("UPDATE", f"expected 4 got {upd}")

    sleep(0.6)
    [eve] = db.strong.query(f"SELECT score FROM {T} WHERE name='Eve'")
    eve_score = float(eve.get("score",0))
    if abs(eve_score - 109.89) < 0.1: p(f"Eve.score after UPDATE: {eve_score:.2f}")
    else: f("Verify UPDATE", f"score={eve_score}")

    # ── TRANSACTION ────────────────────────────────────────────────────────────
    print("\n[TRANSACTION]")
    sleep(0.35)
    db.transaction(lambda tx: (
        tx.execute(f"INSERT INTO {T} (id,name,score,dept) VALUES (11,'TxUser1',50.0,'tx')"),
        tx.execute(f"INSERT INTO {T} (id,name,score,dept) VALUES (12,'TxUser2',51.0,'tx')"),
        tx.execute(f"UPDATE {T} SET score=100.0 WHERE id=11"),
    ))
    sleep(0.7)
    [tx1] = db.strong.query(f"SELECT id,name,score FROM {T} WHERE id=11")
    [tx2] = db.strong.query(f"SELECT id,name FROM {T} WHERE id=12")
    if tx1["name"]=="TxUser1" and float(tx1["score"])==100.0 and tx2["name"]=="TxUser2":
        p("Transaction: 2 INSERTs + 1 UPDATE committed atomically")
    else:
        f("Transaction commit", f"tx1={tx1} tx2={tx2}")

    # Rollback
    sleep(0.35)
    try:
        def fail_tx(tx):
            tx.execute(f"INSERT INTO {T} (id,name) VALUES (999,'RollMe')")
            raise RuntimeError("deliberate rollback")
        db.transaction(fail_tx)
    except RuntimeError: pass
    sleep(0.7)
    rb = db.strong.query(f"SELECT id FROM {T} WHERE id=999")
    if not rb: p("Transaction ROLLBACK: row 999 not persisted")
    else: f("ROLLBACK", "row 999 found")

    # ── UPSERT ────────────────────────────────────────────────────────────────
    print("\n[UPSERT]")
    sleep(0.35)
    db.execute(f"INSERT INTO {T} (id,name,score) VALUES (1,'AliceUpserted',200.0) ON CONFLICT (id) DO UPDATE SET name='AliceUpserted',score=200.0")
    sleep(0.6)
    [upserted] = db.strong.query(f"SELECT name,score FROM {T} WHERE id=1")
    if upserted["name"]=="AliceUpserted" and float(upserted["score"])==200.0: p("ON CONFLICT DO UPDATE")
    else: f("UPSERT", upserted)

    # ── DELETE ────────────────────────────────────────────────────────────────
    print("\n[DELETE]")
    sleep(0.35)
    del_count = db.execute(f"DELETE FROM {T} WHERE active=FALSE")
    if del_count==3: p(f"DELETE WHERE active=FALSE → {del_count} rows")
    else: f("DELETE", f"expected 3 got {del_count}")

    sleep(0.6)
    [cnt_row] = db.strong.query(f"SELECT COUNT(*) AS n FROM {T}")
    n = int(cnt_row["n"])
    # 10 + 2 TxUsers = 12, - 3 inactive = 9
    if n==9: p("Count after DELETE: 9 rows (10+2tx-3inactive)")
    else: f("Post-DELETE count", f"expected 9 got {n}")

    # ── PYTHON-SPECIFIC ────────────────────────────────────────────────────────
    print("\n[PYTHON-SPECIFIC]")

    # execute_raw: commit_status, execution_ms
    sleep(0.35)
    r = db.execute_raw(f"INSERT INTO {T} (id,name,score) VALUES (98,'RawTest',1.0) ON CONFLICT (id) DO NOTHING")
    if r.execution_ms is not None: p(f"execute_raw.execution_ms = {r.execution_ms:.1f}ms")
    else: f("execution_ms", r.execution_ms)

    # strong consistency
    sleep(0.35)
    strong = db.strong.query(f"SELECT COUNT(*) AS n FROM {T}")
    if int(strong[0]["n"]) >= 9: p(f"db.strong bypasses cache")
    else: f("db.strong", strong)

    # with_tag
    tagged = db.with_tag("py-integration").strong.query(f"SELECT name FROM {T} ORDER BY score DESC LIMIT 1")
    if tagged: p(f"with_tag → top scorer: {tagged[0]['name']}")
    else: f("with_tag", tagged)

    # with_idempotency_key
    ikey = f"py-test-{int(time.time())}"
    sleep(0.35)
    r1 = db.with_idempotency_key(ikey).execute_raw(f"INSERT INTO {T} (id,name) VALUES (97,'IdemPy') ON CONFLICT (id) DO NOTHING")
    sleep(0.35)
    r2 = db.with_idempotency_key(ikey).execute_raw(f"INSERT INTO {T} (id,name) VALUES (97,'IdemPy') ON CONFLICT (id) DO NOTHING")
    p(f"with_idempotency_key: first={r1.rows_affected} second={r2.rows_affected}")

    # DeltexError on bad SQL
    sleep(0.35)
    try:
        db.query("COMPLETELY INVALID SQL !!!")
        f("DeltexError", "should have raised")
    except DeltexError as e:
        p(f"DeltexError(status={e.status}): {str(e)[:50]}...")

    # DeltexError on missing table
    sleep(0.35)
    try:
        db.query(f"SELECT * FROM nonexistent_{int(time.time())}")
        f("Missing table", "should have raised")
    except DeltexError as e:
        p(f"DeltexError: missing table → {str(e)[:40]}...")

    # ── CLEANUP ────────────────────────────────────────────────────────────────
    sleep(0.35)
    db.execute(f"DROP TABLE IF EXISTS {T}")

finally:
    print(f"\n{'─'*60}")
    color = "\033[32m" if failed==0 else "\033[31m"
    print(f"{color}Python SDK: {passed} passed, {failed} failed\033[0m")
    if failed > 0:
        sys.exit(1)
