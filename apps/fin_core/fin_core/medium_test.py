"""
Medium-Scale Concurrency Test
------------------------------
Phase 1 : 1,000 Receipts   — 10 parallel threads
Phase 2 : 2,000 Fin Orders — 20 parallel threads (high-contention on shared stock)

Each Receipt  : 3 random items, qty ∈ [1,10], rate ∈ [100,500]
Each Fin Order: 2 random items, qty ∈ [1,5]

Postings are created INLINE (no background queue) so the audit runs immediately.
Naming-series contention under REPEATABLE READ is absorbed by jittered-backoff retries.
Results → /tmp/medium_results.json
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
# Configuration
# ---------------------------------------------------------------------------
SITE                 = "fincore.local"
WAREHOUSE            = "STRESS_WAREHOUSE"

N_RECEIPTS           = 1_000
N_FIN_ORDERS         = 2_000

RECEIPT_ITEMS        = 3        # items sampled per receipt
ORDER_ITEMS          = 2        # items sampled per fin order

RECEIPT_QTY_MAX      = 10
RECEIPT_RATE_MIN     = 100
RECEIPT_RATE_MAX     = 500

ORDER_QTY_MAX        = 5

CONCURRENCY_RECEIPTS = 10
CONCURRENCY_ORDERS   = 20       # high-contention stress

ITEMS     = [f"STRESS_ITEM_{i:02d}" for i in range(1, 11)]
CUSTOMERS = [f"STRESS_CUSTOMER_{i:02d}_AR" for i in range(1, 6)]

# ---------------------------------------------------------------------------
# Retry configuration (naming-series tabSeries row under REPEATABLE READ)
# ---------------------------------------------------------------------------
_MAX_RETRIES  = 8
_RETRY_ERRORS = ("serialize", "deadlock", "lock wait timeout", "could not serialize")


def _is_retryable(exc: Exception) -> bool:
    return any(kw in str(exc).lower() for kw in _RETRY_ERRORS)


# ---------------------------------------------------------------------------
# Thread worker — isolated frappe session with jittered exponential backoff
# ---------------------------------------------------------------------------
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
                delay = (attempt + 1) * random.uniform(0.03, 0.12)
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
def _submit_receipt(idx):
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
                "qty":            float(random.randint(1, RECEIPT_QTY_MAX)),
                "rate":           float(random.randint(RECEIPT_RATE_MIN, RECEIPT_RATE_MAX)),
                "amount":         0.0,
            }
            for item in items
        ],
    })
    doc.insert(ignore_permissions=True)
    doc.submit()
    frappe.db.commit()

    # Idempotent — skips if units already exist (guards against double-enqueue)
    run_receipt_process(doc.name)
    frappe.db.commit()

    # Postings inline
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

    return {
        "name":         doc.name,
        "total_amount": doc.total_amount,
        "n_units":      len(unit_names),
        "elapsed_s":    time.perf_counter() - t0,
    }


# ---------------------------------------------------------------------------
# Fin Order worker
# ---------------------------------------------------------------------------
def _submit_fin_order(idx):
    from fin_core.engine.posting_service import create_postings_from_units

    items    = random.sample(ITEMS, k=ORDER_ITEMS)
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
                "rate":           float(random.randint(RECEIPT_RATE_MIN, RECEIPT_RATE_MAX)),
                "amount":         0.0,
            }
            for item in items
        ],
    })
    doc.insert(ignore_permissions=True)
    doc.submit()
    frappe.db.commit()

    # Postings inline — engine already ran in on_submit synchronously
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

    return {
        "name":         doc.name,
        "total_amount": doc.total_amount,
        "n_units":      len(unit_names),
        "elapsed_s":    time.perf_counter() - t0,
    }


# ---------------------------------------------------------------------------
# Generic batch executor
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
                tb = traceback.format_exc()
                errors.append({"idx": idx, "error": str(exc), "trace": tb.splitlines()[-3:]})
                print(f"  ERROR [{label} #{idx}]: {str(exc).splitlines()[0][:100]}")

            interval = max(1, count // 20)  # progress every 5 %
            if completed % interval == 0 or completed == count:
                elapsed = time.perf_counter() - t_start
                tps = completed / elapsed if elapsed > 0 else 0
                print(f"  [{label}] {completed}/{count}  "
                      f"errors={len(errors)}  {tps:.1f} docs/s")

    wall = time.perf_counter() - t_start
    lats = sorted(r["elapsed_s"] for r in results)

    def _pct(p):
        if not lats:
            return 0.0
        return lats[int(len(lats) * p / 100)]

    # Classify errors
    naming_errs = [e for e in errors
                   if any(k in e["error"].lower() for k in ("serialize", "deadlock"))]
    stock_errs  = [e for e in errors if "insufficient stock" in e["error"].lower()]
    other_errs  = [e for e in errors
                   if e not in naming_errs and e not in stock_errs]

    stats = {
        "label":           label,
        "total":           count,
        "success":         len(results),
        "error_count":     len(errors),
        "errors_naming":   len(naming_errs),
        "errors_stock":    len(stock_errs),
        "errors_other":    len(other_errs),
        "wall_time_s":     round(wall, 2),
        "throughput_ps":   round(len(results) / wall, 2) if wall > 0 else 0,
        "apt_s":           round(wall / count, 3),
        "latency_p50_s":   round(_pct(50), 3),
        "latency_p95_s":   round(_pct(95), 3),
        "latency_p99_s":   round(_pct(99), 3),
        "error_sample":    errors[:5],
    }
    return stats, results


# ---------------------------------------------------------------------------
# Phase summaries
# ---------------------------------------------------------------------------
def _print_phase(label, stats):
    print(f"\n  ── {label} Results ──────────────────────────────")
    print(f"  Success          : {stats['success']}/{stats['total']}")
    print(f"  Errors           : {stats['error_count']}"
          f"  (naming={stats['errors_naming']} "
          f"stock={stats['errors_stock']} "
          f"other={stats['errors_other']})")
    print(f"  Wall time        : {stats['wall_time_s']}s")
    print(f"  Throughput       : {stats['throughput_ps']} docs/s")
    print(f"  APT              : {stats['apt_s']}s/doc")
    print(f"  Latency p50/p95/p99: "
          f"{stats['latency_p50_s']}s / "
          f"{stats['latency_p95_s']}s / "
          f"{stats['latency_p99_s']}s")
    if stats["error_sample"]:
        print(f"  Error sample:")
        for e in stats["error_sample"]:
            print(f"    #{e['idx']}: {e['error'][:120]}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run():
    print("\n" + "=" * 65)
    print("MEDIUM-SCALE CONCURRENCY TEST")
    print(f"  Receipts  : {N_RECEIPTS:,}  threads={CONCURRENCY_RECEIPTS}")
    print(f"  Fin Orders: {N_FIN_ORDERS:,}  threads={CONCURRENCY_ORDERS}")
    print("=" * 65)

    t_global = time.perf_counter()

    # ── Phase 1: Receipts ──────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"PHASE 1: {N_RECEIPTS:,} Receipts  (concurrency={CONCURRENCY_RECEIPTS})")
    print(f"{'─'*65}")
    r_stats, _ = _run_batch("Receipt", _submit_receipt, N_RECEIPTS, CONCURRENCY_RECEIPTS)
    _print_phase("Receipts", r_stats)

    # ── Snapshot stock before orders ───────────────────────────────────────
    stock_snap = frappe.db.sql("""
        SELECT item_code,
               SUM(qty)   AS total_qty,
               COUNT(*)   AS n_units,
               SUM(amount) AS total_amount
        FROM "tabStock Unit"
        WHERE warehouse = %s AND status = 'Available' AND is_active = 1
        GROUP BY item_code ORDER BY item_code
    """, (WAREHOUSE,), as_dict=True)

    total_available = sum(float(r.total_qty) for r in stock_snap)
    total_value     = sum(float(r.total_amount or 0) for r in stock_snap)
    print(f"\n  ── Stock snapshot before orders ──")
    for r in stock_snap:
        print(f"    {r.item_code}: qty={r.total_qty:.0f}  units={r.n_units}  value={r.total_amount:,.0f}")
    print(f"  Total available qty  : {total_available:,.0f}")
    print(f"  Total inventory value: {total_value:,.2f} UAH")

    est_demand = N_FIN_ORDERS * ORDER_ITEMS * ((1 + ORDER_QTY_MAX) / 2)
    print(f"  Estimated demand     : ~{est_demand:,.0f} (spread across {len(ITEMS)} items)")

    # ── Phase 2: Fin Orders ────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"PHASE 2: {N_FIN_ORDERS:,} Fin Orders  (concurrency={CONCURRENCY_ORDERS})")
    print(f"{'─'*65}")
    fo_stats, _ = _run_batch("FinOrder", _submit_fin_order, N_FIN_ORDERS, CONCURRENCY_ORDERS)
    _print_phase("Fin Orders", fo_stats)

    total_wall = time.perf_counter() - t_global

    summary = {
        "receipts":        r_stats,
        "fin_orders":      fo_stats,
        "total_wall_time_s": round(total_wall, 2),
    }
    with open("/tmp/medium_results.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'=' * 65}")
    print(f"TOTAL WALL TIME: {total_wall:.1f}s")
    print(f"Results → /tmp/medium_results.json")
    print(f"{'=' * 65}")
    return summary
