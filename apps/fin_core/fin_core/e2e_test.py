"""
E2E Controlled Test — MAIN_WAREHOUSE
======================================
Phase A : 50 Receipts  — submitted sequentially, UTXO created by background workers
Phase B : 100 Fin Orders — submitted with 6 threads (matching worker count),
                           postings created by background workers
Phase C : Integrity audit (in e2e_audit.py)

Relies on 4-6 active RQ workers already running (bench start).
"""
import frappe
import random
import time
import json
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from frappe.utils import today

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SITE       = "fincore.local"
WAREHOUSE  = "MAIN_WAREHOUSE"
ITEMS      = ["ITEM_STEEL_PIPE", "ITEM_WOOD_PLANK"]
CUSTOMERS  = ["CUSTOMER_A_AR", "CUSTOMER_B_AR"]
AP_ACCOUNT = "ACCOUNTS_PAYABLE"

N_RECEIPTS   = 50
N_FIN_ORDERS = 100

RECEIPT_ITEMS    = 2        # all receipts use both items
RECEIPT_QTY_MIN  = 5
RECEIPT_QTY_MAX  = 20
RECEIPT_RATE_MIN = 100
RECEIPT_RATE_MAX = 1000

ORDER_ITEMS_MAX  = 2
ORDER_QTY_MIN    = 1
ORDER_QTY_MAX    = 8
ORDER_RATE_MIN   = 100
ORDER_RATE_MAX   = 1500

CONCURRENCY_ORDERS = 6     # match worker count

QUEUE_DRAIN_POLL_S  = 2    # seconds between queue polls
QUEUE_DRAIN_MAX_S   = 300  # max wait per drain


# ---------------------------------------------------------------------------
# Clean room — scoped to MAIN_WAREHOUSE
# ---------------------------------------------------------------------------
def clean_warehouse():
    print(f"\n  [clean] removing MAIN_WAREHOUSE data...")

    frappe.db.sql("""
        DELETE FROM "tabAnalytical Posting"
        WHERE source_name IN (
            SELECT name FROM tabReceipt     WHERE warehouse = 'MAIN_WAREHOUSE'
            UNION ALL
            SELECT name FROM "tabFin Order" WHERE warehouse = 'MAIN_WAREHOUSE'
        )
    """)
    frappe.db.sql("""
        DELETE FROM "tabFin Order Stock Unit"
        WHERE parent IN (SELECT name FROM "tabFin Order" WHERE warehouse = 'MAIN_WAREHOUSE')
    """)
    frappe.db.sql("DELETE FROM \"tabStock Unit\" WHERE warehouse = 'MAIN_WAREHOUSE'")
    frappe.db.sql("""
        DELETE FROM "tabFin Order Item"
        WHERE parent IN (SELECT name FROM "tabFin Order" WHERE warehouse = 'MAIN_WAREHOUSE')
    """)
    frappe.db.sql("DELETE FROM \"tabFin Order\" WHERE warehouse = 'MAIN_WAREHOUSE'")
    frappe.db.sql("""
        DELETE FROM "tabReceipt Item"
        WHERE parent IN (SELECT name FROM tabReceipt WHERE warehouse = 'MAIN_WAREHOUSE')
    """)
    frappe.db.sql("DELETE FROM tabReceipt WHERE warehouse = 'MAIN_WAREHOUSE'")
    frappe.db.commit()
    print("  [clean] done")


# ---------------------------------------------------------------------------
# Queue drain — wait until all RQ queues are empty and no jobs running
# ---------------------------------------------------------------------------
def _queue_depth() -> tuple[int, int]:
    from rq import Queue
    from frappe.utils.background_jobs import get_redis_conn
    conn = get_redis_conn()
    pending, running = 0, 0
    for q_name in ("default", "long", "short"):
        q = Queue(q_name, connection=conn)
        pending += len(q)
        running += q.started_job_registry.count
    return pending, running


def wait_for_queue_drain(label: str, max_wait: int = QUEUE_DRAIN_MAX_S) -> None:
    print(f"\n  [queue] waiting for workers to drain ({label})...")
    t0 = time.perf_counter()
    last_log = -10

    while time.perf_counter() - t0 < max_wait:
        pending, running = _queue_depth()
        elapsed = time.perf_counter() - t0

        if pending == 0 and running == 0:
            print(f"  [queue] drained in {elapsed:.1f}s")
            return

        if elapsed - last_log >= 10:
            print(f"  [queue] {elapsed:.0f}s — pending={pending} running={running}")
            last_log = elapsed

        time.sleep(QUEUE_DRAIN_POLL_S)

    pending, running = _queue_depth()
    print(f"  [queue] WARNING: timed out after {max_wait}s "
          f"(pending={pending} running={running})")


# ---------------------------------------------------------------------------
# Retry config (naming-series contention under REPEATABLE READ)
# ---------------------------------------------------------------------------
_MAX_RETRIES  = 6
_RETRY_ERRORS = ("serialize", "deadlock", "lock wait timeout", "could not serialize")


def _is_retryable(exc: Exception) -> bool:
    return any(kw in str(exc).lower() for kw in _RETRY_ERRORS)


def _frappe_worker(fn, *args, **kwargs):
    frappe.init(site=SITE)
    frappe.connect()
    frappe.set_user("Administrator")
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            result = fn(*args, **kwargs)
            frappe.db.commit()
            frappe.destroy()
            return result
        except Exception as exc:
            last_exc = exc
            try:
                frappe.db.rollback()
            except Exception:
                pass
            if _is_retryable(exc) and attempt < _MAX_RETRIES - 1:
                time.sleep((attempt + 1) * random.uniform(0.03, 0.12))
                continue
            break
    try:
        frappe.destroy()
    except Exception:
        pass
    raise last_exc


# ---------------------------------------------------------------------------
# Receipt worker — submit only; workers handle UTXO async
# ---------------------------------------------------------------------------
def _submit_receipt(idx: int) -> dict:
    t0 = time.perf_counter()
    doc = frappe.get_doc({
        "doctype":            "Receipt",
        "warehouse":          WAREHOUSE,
        "customer_analytics": AP_ACCOUNT,
        "posting_date":       today(),
        "items": [
            {
                "item_analytics": item,
                "qty":            float(random.randint(RECEIPT_QTY_MIN, RECEIPT_QTY_MAX)),
                "rate":           float(random.randint(RECEIPT_RATE_MIN, RECEIPT_RATE_MAX)),
                "amount":         0.0,
            }
            for item in ITEMS  # always both items
        ],
    })
    doc.insert(ignore_permissions=True)
    doc.submit()
    # commit triggers enqueue_after_commit → run_receipt_process lands in RQ
    return {
        "name":         doc.name,
        "total_amount": doc.total_amount,
        "elapsed_s":    time.perf_counter() - t0,
    }


# ---------------------------------------------------------------------------
# Fin Order worker — UTXO spend happens synchronously in on_submit;
# postings are enqueued to background workers
# ---------------------------------------------------------------------------
def _submit_fin_order(idx: int) -> dict:
    n_items  = random.randint(1, ORDER_ITEMS_MAX)
    items    = random.sample(ITEMS, k=n_items)
    customer = random.choice(CUSTOMERS)
    t0 = time.perf_counter()
    doc = frappe.get_doc({
        "doctype":            "Fin Order",
        "customer_analytics": customer,
        "warehouse":          WAREHOUSE,
        "posting_date":       today(),
        "items": [
            {
                "item_analytics": item,
                "qty":            float(random.randint(ORDER_QTY_MIN, ORDER_QTY_MAX)),
                "rate":           float(random.randint(ORDER_RATE_MIN, ORDER_RATE_MAX)),
                "amount":         0.0,
            }
            for item in items
        ],
    })
    doc.insert(ignore_permissions=True)
    doc.submit()
    return {
        "name":         doc.name,
        "total_amount": doc.total_amount,
        "elapsed_s":    time.perf_counter() - t0,
    }


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------
def _run_batch(label, worker_fn, count, concurrency):
    results, errors = [], []
    t_start = time.perf_counter()
    completed = 0

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_frappe_worker, worker_fn, i): i for i in range(count)}
        for future in as_completed(futures):
            idx = futures[future]
            completed += 1
            try:
                results.append(future.result())
            except Exception as exc:
                tb_lines = traceback.format_exc().splitlines()[-3:]
                errors.append({"idx": idx, "error": str(exc), "trace": tb_lines})
                print(f"  ERROR [{label} #{idx}]: {str(exc).splitlines()[0][:100]}")

            interval = max(1, count // 10)
            if completed % interval == 0 or completed == count:
                elapsed = time.perf_counter() - t_start
                tps = completed / elapsed if elapsed > 0 else 0
                print(f"  [{label}] {completed}/{count}  errors={len(errors)}  {tps:.1f} docs/s")

    wall = time.perf_counter() - t_start
    lats = sorted(r["elapsed_s"] for r in results)

    def _p(pct):
        return lats[int(len(lats) * pct / 100)] if lats else 0.0

    naming_errs = [e for e in errors if any(k in e["error"].lower()
                   for k in ("serialize", "deadlock"))]
    stock_errs  = [e for e in errors if "insufficient stock" in e["error"].lower()]
    other_errs  = [e for e in errors if e not in naming_errs and e not in stock_errs]

    stats = {
        "label":          label,
        "total":          count,
        "success":        len(results),
        "error_count":    len(errors),
        "errors_naming":  len(naming_errs),
        "errors_stock":   len(stock_errs),
        "errors_other":   len(other_errs),
        "wall_time_s":    round(wall, 2),
        "throughput_ps":  round(len(results) / wall, 2) if wall > 0 else 0,
        "apt_s":          round(wall / count, 3),
        "p50_s":          round(_p(50), 3),
        "p95_s":          round(_p(95), 3),
        "p99_s":          round(_p(99), 3),
        "error_sample":   errors[:3],
    }
    return stats


def _print_stats(stats):
    s = stats
    print(f"\n  ── {s['label']} ─────────────────────────────────────────")
    print(f"  Success     : {s['success']}/{s['total']}")
    print(f"  Errors      : {s['error_count']}  "
          f"(naming={s['errors_naming']} stock={s['errors_stock']} other={s['errors_other']})")
    print(f"  Wall time   : {s['wall_time_s']}s")
    print(f"  Throughput  : {s['throughput_ps']} docs/s")
    print(f"  APT         : {s['apt_s']}s/doc")
    print(f"  Lat p50/p95/p99 : {s['p50_s']}s / {s['p95_s']}s / {s['p99_s']}s")
    if s["error_sample"]:
        print("  Error sample:")
        for e in s["error_sample"]:
            print(f"    #{e['idx']}: {e['error'][:120]}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run():
    print("\n" + "=" * 62)
    print("E2E CONTROLLED TEST — MAIN_WAREHOUSE")
    print(f"  Receipts  : {N_RECEIPTS}   items=2  qty=[{RECEIPT_QTY_MIN},{RECEIPT_QTY_MAX}]")
    print(f"  Fin Orders: {N_FIN_ORDERS}  items=1-2  qty=[{ORDER_QTY_MIN},{ORDER_QTY_MAX}]")
    print(f"  Workers   : 6 background RQ workers")
    print("=" * 62)

    # ── Pre-flight check ────────────────────────────────────────────────────
    pending, running = _queue_depth()
    print(f"\n  Pre-flight queue: pending={pending} running={running}")
    if pending + running > 0:
        print("  WARNING: queue not empty before test — waiting...")
        wait_for_queue_drain("pre-flight")

    clean_warehouse()

    t_global = time.perf_counter()

    # ── Phase A: Receipts ───────────────────────────────────────────────────
    print(f"\n{'─' * 62}")
    print(f"PHASE A — {N_RECEIPTS} Receipts  (sequential submit, workers do UTXO)")
    print(f"{'─' * 62}")
    r_stats = _run_batch("Receipt", _submit_receipt, N_RECEIPTS, concurrency=4)
    _print_stats(r_stats)

    # Stock snapshot immediately after submission (before workers finish)
    pending, _ = _queue_depth()
    print(f"\n  Queue depth after receipt submission: {pending} jobs pending")

    # Wait for UTXO units to be created before submitting orders
    wait_for_queue_drain("UTXO creation")

    # Verify stock was actually created
    stock = frappe.db.sql("""
        SELECT item_code,
               COUNT(*) FILTER (WHERE is_active=1 AND status='Available') AS available_units,
               SUM(qty)  FILTER (WHERE is_active=1 AND status='Available') AS available_qty
        FROM "tabStock Unit"
        WHERE warehouse = 'MAIN_WAREHOUSE'
        GROUP BY item_code ORDER BY item_code
    """, as_dict=True)
    print(f"\n  ── Stock after UTXO processing ──")
    total_avail = 0
    for r in stock:
        print(f"    {r.item_code}: {r.available_units} units  qty={r.available_qty:.0f}")
        total_avail += float(r.available_qty or 0)
    print(f"    Total available qty: {total_avail:.0f}")

    est_demand = N_FIN_ORDERS * ((1 + ORDER_ITEMS_MAX) / 2) * ((ORDER_QTY_MIN + ORDER_QTY_MAX) / 2)
    print(f"    Estimated demand  : ~{est_demand:.0f}")
    if total_avail < est_demand:
        print("    WARNING: potential stock shortage — some orders may fail")

    # ── Phase B: Fin Orders ─────────────────────────────────────────────────
    print(f"\n{'─' * 62}")
    print(f"PHASE B — {N_FIN_ORDERS} Fin Orders  (concurrency={CONCURRENCY_ORDERS}, workers do postings)")
    print(f"{'─' * 62}")
    fo_stats = _run_batch("FinOrder", _submit_fin_order, N_FIN_ORDERS, concurrency=CONCURRENCY_ORDERS)
    _print_stats(fo_stats)

    # Wait for postings and stock-transaction refresh to complete
    wait_for_queue_drain("posting creation")

    total_wall = time.perf_counter() - t_global

    summary = {
        "receipts":          r_stats,
        "fin_orders":        fo_stats,
        "total_wall_time_s": round(total_wall, 2),
    }
    with open("/tmp/e2e_results.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'=' * 62}")
    print(f"TOTAL WALL TIME (submit + worker drain): {total_wall:.1f}s")
    print("Results → /tmp/e2e_results.json")
    print(f"{'=' * 62}")

    return summary
