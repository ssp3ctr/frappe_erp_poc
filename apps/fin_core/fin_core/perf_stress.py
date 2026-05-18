"""
Stress test: 1,000 Receipts → 10,000 Fin Orders.

Architecture:
  - ThreadPoolExecutor: each thread owns its own frappe session (frappe.local is thread-local).
  - Receipts run engine synchronously (no queue) so stock is available immediately.
  - Fin Orders run engine synchronously; SELECT FOR UPDATE prevents race conditions.
  - Posting service is called inline after each batch for deterministic results.
  - Metrics logged to /tmp/perf_results.json after completion.
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
SITE            = "fincore.local"
WAREHOUSE       = "STRESS_WAREHOUSE"
N_RECEIPTS      = 1_000
N_FIN_ORDERS    = 10_000
RECEIPT_ITEMS   = 5          # items per receipt
ORDER_MAX_ITEMS = 3          # max items per fin order
QTY_PER_RECEIPT = 100        # qty of each item per receipt line
ORDER_QTY_MAX   = 5          # max qty per fin order line
CONCURRENCY     = 8          # thread-pool size (naming-series bottleneck: keep ≤ 10)
RECEIPT_TMPL    = "RECEIPT_POSTINGS"
FIN_ORDER_TMPL  = "FIN_ORDER_POSTINGS"

# 10 test items (pre-seeded in setup)
ITEMS = [f"STRESS_ITEM_{i:02d}" for i in range(1, 11)]
# 5 customer AR accounts
CUSTOMERS = [f"STRESS_CUSTOMER_{i:02d}_AR" for i in range(1, 6)]


# ---------------------------------------------------------------------------
# Thread-safe frappe session helper
# ---------------------------------------------------------------------------

_RETRY_ERRORS = ("serialize", "deadlock", "lock wait timeout", "could not serialize")
_MAX_RETRIES  = 5


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in _RETRY_ERRORS)


def _frappe_worker(fn, *args, **kwargs):
    """
    Wrap a callable in its own frappe session.
    frappe.local is threading.local() so each thread is isolated.
    Retries up to _MAX_RETRIES times on transient serialization/deadlock errors
    (most common under high concurrency: naming-series tabSeries row contention).
    """
    import random as _random

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
                # Jittered backoff: 20–100 ms × (attempt+1)
                delay = (attempt + 1) * _random.uniform(0.02, 0.10)
                time.sleep(delay)
                continue
            break

    try:
        frappe.destroy()
    except Exception:
        pass
    raise last_exc


# ---------------------------------------------------------------------------
# Receipt worker
# ---------------------------------------------------------------------------

def _submit_receipt(receipt_idx):
    """Create + submit one Receipt, run engine + postings synchronously."""
    from fin_core.engine.utxo_engine import run_receipt_process
    from fin_core.engine.posting_service import create_postings_from_units

    items = random.sample(ITEMS, k=RECEIPT_ITEMS)
    t0 = time.perf_counter()

    doc = frappe.get_doc({
        "doctype":            "Receipt",
        "warehouse":          WAREHOUSE,
        "customer_analytics": "STRESS_AP",
        "posting_date":       today(),
        "items": [
            {
                "item_analytics": item,
                "qty":            float(QTY_PER_RECEIPT),
                "rate":           float(random.randint(50, 500)),
                "amount":         0.0,
            }
            for item in items
        ],
    })
    doc.insert(ignore_permissions=True)
    doc.submit()
    frappe.db.commit()
    run_receipt_process(doc.name)
    frappe.db.commit()

    # Create postings inline (bypass background queue for audit accuracy)
    unit_names = [r["name"] for r in frappe.get_all(
        "Stock Unit",
        filters={"source_doctype": "Receipt", "source_name": doc.name},
        fields=["name"],
    )]
    if unit_names:
        create_postings_from_units(
            unit_names=unit_names,
            source_doctype="Receipt",
            source_name=doc.name,
            posting_date=str(doc.posting_date),
        )
        frappe.db.commit()

    elapsed = time.perf_counter() - t0
    return {"name": doc.name, "total_amount": doc.total_amount, "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Fin Order worker
# ---------------------------------------------------------------------------

def _submit_fin_order(order_idx):
    """Create + submit one Fin Order, run engine + postings synchronously."""
    from fin_core.engine.posting_service import create_postings_from_units

    n_items = random.randint(1, ORDER_MAX_ITEMS)
    items   = random.sample(ITEMS, k=n_items)
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
                "qty":            float(random.randint(1, ORDER_QTY_MAX)),
                "rate":           float(random.randint(100, 1000)),
                "amount":         0.0,
            }
            for item in items
        ],
    })
    doc.insert(ignore_permissions=True)
    doc.submit()
    frappe.db.commit()

    # Create postings inline (bypass background queue for audit accuracy).
    # Fin Order engine runs synchronously in on_submit; units exist after commit.
    unit_names = [r["name"] for r in frappe.get_all(
        "Stock Unit",
        filters={"source_doctype": "Fin Order", "source_name": doc.name},
        fields=["name"],
    )]
    if unit_names:
        create_postings_from_units(
            unit_names=unit_names,
            source_doctype="Fin Order",
            source_name=doc.name,
            posting_date=str(doc.posting_date),
        )
        frappe.db.commit()

    elapsed = time.perf_counter() - t0
    return {"name": doc.name, "total_amount": doc.total_amount, "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Batch runner with progress tracking
# ---------------------------------------------------------------------------

def _run_batch(label, worker_fn, indices, concurrency):
    results   = []
    errors    = []
    t_start   = time.perf_counter()
    completed = 0

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(_frappe_worker, worker_fn, idx): idx
            for idx in indices
        }
        for future in as_completed(futures):
            idx = futures[future]
            completed += 1
            try:
                results.append(future.result())
            except Exception as exc:
                errors.append({"idx": idx, "error": str(exc)})
                msg = str(exc).splitlines()[0][:100]
                print(f"  ERROR [{label} #{idx}]: {msg}")

            if completed % max(1, len(indices) // 10) == 0 or completed == len(indices):
                elapsed = time.perf_counter() - t_start
                tps = completed / elapsed if elapsed > 0 else 0
                print(f"  [{label}] {completed}/{len(indices)}  "
                      f"errors={len(errors)}  {tps:.1f} docs/s")

    total_elapsed = time.perf_counter() - t_start
    latencies = [r["elapsed_s"] for r in results]
    p50 = sorted(latencies)[len(latencies)//2] if latencies else 0
    p95 = sorted(latencies)[int(len(latencies)*0.95)] if latencies else 0

    # Classify errors: naming-series vs UTXO (insufficient stock) vs other
    naming_errs    = [e for e in errors if "serialize" in e["error"].lower() or "deadlock" in e["error"].lower()]
    stock_errs     = [e for e in errors if "insufficient stock" in e["error"].lower()]
    other_errs     = [e for e in errors if e not in naming_errs and e not in stock_errs]

    stats = {
        "label":              label,
        "total":              len(indices),
        "success":            len(results),
        "errors":             len(errors),
        "errors_naming":      len(naming_errs),
        "errors_stock":       len(stock_errs),
        "errors_other":       len(other_errs),
        "wall_time_s":        round(total_elapsed, 2),
        "throughput_ps":      round(len(results) / total_elapsed, 1) if total_elapsed > 0 else 0,
        "latency_p50_s":      round(p50, 3),
        "latency_p95_s":      round(p95, 3),
        "error_details":      errors[:10],
    }
    return stats, results


# ---------------------------------------------------------------------------
# Main phases
# ---------------------------------------------------------------------------

def phase_receipts():
    print(f"\n{'='*60}")
    print(f"PHASE 1: {N_RECEIPTS} Receipts  (concurrency={CONCURRENCY})")
    print(f"{'='*60}")
    stats, results = _run_batch("Receipt", _submit_receipt, range(N_RECEIPTS), CONCURRENCY)
    _print_stats(stats)
    return stats, results


def phase_fin_orders():
    print(f"\n{'='*60}")
    print(f"PHASE 2: {N_FIN_ORDERS} Fin Orders  (concurrency={CONCURRENCY})")
    print(f"{'='*60}")

    # Check available stock before starting
    stock_check = frappe.db.sql("""
        SELECT item_code, SUM(qty) AS total_qty, COUNT(*) AS n_units
        FROM "tabStock Unit"
        WHERE warehouse = %s AND status = 'Available' AND is_active = 1
        GROUP BY item_code
        ORDER BY item_code
    """, (WAREHOUSE,), as_dict=True)

    total_available = sum(r.total_qty for r in stock_check)
    print(f"\n  Available stock before orders:")
    for r in stock_check:
        print(f"    {r.item_code}: qty={r.total_qty:.0f}  units={r.n_units}")
    print(f"  Total available qty: {total_available:.0f}")

    # Estimate demand: N_FIN_ORDERS × avg_items × avg_qty
    est_demand = N_FIN_ORDERS * ((1 + ORDER_MAX_ITEMS) / 2) * ((1 + ORDER_QTY_MAX) / 2)
    print(f"  Estimated demand:    {est_demand:.0f}")
    if total_available < est_demand * 0.8:
        print(f"  WARNING: stock may be insufficient — orders may fail with 'Insufficient stock'")

    stats, results = _run_batch("FinOrder", _submit_fin_order, range(N_FIN_ORDERS), CONCURRENCY)
    _print_stats(stats)
    return stats, results


def _print_stats(s):
    print(f"\n  --- {s['label']} Results ---")
    print(f"  Success:          {s['success']}/{s['total']}")
    print(f"  Errors total:     {s['errors']}")
    print(f"    Naming/serial:  {s['errors_naming']}")
    print(f"    Insufficient:   {s['errors_stock']}")
    print(f"    Other:          {s['errors_other']}")
    print(f"  Wall time:        {s['wall_time_s']}s")
    print(f"  Throughput:       {s['throughput_ps']} docs/s")
    print(f"  Latency p50:      {s['latency_p50_s']}s")
    print(f"  Latency p95:      {s['latency_p95_s']}s")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    print("\n" + "="*60)
    print("UTXO STRESS TEST")
    print("="*60)

    t_global = time.perf_counter()

    r_stats, r_results = phase_receipts()
    fo_stats, fo_results = phase_fin_orders()

    total_time = time.perf_counter() - t_global

    summary = {
        "receipts":   r_stats,
        "fin_orders": fo_stats,
        "total_wall_time_s": round(total_time, 2),
    }

    with open("/tmp/perf_results.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'='*60}")
    print(f"TOTAL WALL TIME: {total_time:.1f}s")
    print(f"Results saved to /tmp/perf_results.json")
    print(f"{'='*60}")

    return summary
