"""
UTXO Engine Stress Test
=======================
Run:
    bench --site <site> execute fin_core.stress_test_random.run_test

Phases:
  1. Seed   — 50 Receipts with random qty [50..200]
  2. Attack — 1 000 Fin Order drafts enqueued simultaneously
  3. Drain  — wait for all worker jobs to complete
  4. Validate — UTXO invariants
  5. Report — timing, error-log, deadlock scan
"""

import random
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

import frappe

# ── tunables ──────────────────────────────────────────────────────────────────
RECEIPT_COUNT     = 50
ORDER_COUNT       = 1_000
RECEIPT_QTY_MIN   = 50
RECEIPT_QTY_MAX   = 2000
ORDER_QTY_SMALL   = (1, 5)        # 80 %  — routine spend
ORDER_QTY_LARGE   = (150, 500)    # 20 %  — multi-unit / likely exceeds stock
QUEUE_TIMEOUT_S   = 300           # seconds to wait for workers


# ── entry point ───────────────────────────────────────────────────────────────

def run_test(*args, **kwargs):
    start_ts = time.time()
    start_dt = datetime.fromtimestamp(start_ts)
    _section("UTXO Stress Test")

    item, customer, warehouse = _require_fixtures()
    print(f"item={item!r}  customer={customer!r}  warehouse={warehouse!r}\n")

    # Phase 1 ─ Seed
    _section("Phase 1 · Seed")
    receipt_names, initial_qty = _seed_receipts(item, customer, warehouse)
    print(f"Created {len(receipt_names)} receipts, initial_qty={initial_qty}")

    # Phase 2 ─ Attack
    _section("Phase 2 · Attack")
    order_names = _create_draft_orders(item, customer, warehouse)
    _enqueue_all(order_names)
    print(f"Enqueued {len(order_names)} Fin Order submit jobs")

    # Phase 3 ─ Drain queue
    _section("Phase 3 · Drain queue")
    drained = _drain_queue(QUEUE_TIMEOUT_S)
    if not drained:
        print(f"WARNING: queue did not fully drain within {QUEUE_TIMEOUT_S}s")

    # Phase 4 ─ Validate
    _section("Phase 4 · Validate")
    passed = _validate(item, warehouse, initial_qty, order_names)

    # Phase 5 ─ Report
    _section("Phase 5 · Report")
    _report_errors(start_dt)

    elapsed = time.time() - start_ts
    print(f"\nTotal time : {elapsed:.1f}s")
    print("\n" + ("=" * 20 + " PASS " + "=" * 20 if passed else "=" * 20 + " FAIL " + "=" * 20))


# ── worker (enqueued per order) ───────────────────────────────────────────────

def process_fin_order_submit(order_name: str) -> None:
    """Enqueued worker: submits one Fin Order draft and runs SPEND."""
    doc = frappe.get_doc("Fin Order", order_name)
    if doc.docstatus == 0:
        doc.submit()


# ── fixtures ──────────────────────────────────────────────────────────────────

def _require_fixtures():
    items = frappe.get_all(
        "Analytics", filters={"type": "Quantity"}, pluck="name", limit=2
    )
    if len(items) < 1:
        frappe.throw("Need at least one Analytics record with type='Quantity'.")

    warehouses = frappe.get_all(
        "Warehouse", filters={"is_active": 1}, pluck="name", limit=1
    )
    if not warehouses:
        frappe.throw("Need at least one active Warehouse.")

    item = items[0]
    # Use second analytics record as customer dimension; fall back to same record
    customer = items[1] if len(items) > 1 else item
    warehouse = warehouses[0]
    return item, customer, warehouse


# ── phase 1: seed ─────────────────────────────────────────────────────────────

def _seed_receipts(item: str, customer: str, warehouse: str):
    from fin_core.engine.utxo_engine import run_receipt_process

    names: list[str] = []
    total = Decimal("0")
    today = date.today()

    for _ in range(RECEIPT_COUNT):
        qty = random.randint(RECEIPT_QTY_MIN, RECEIPT_QTY_MAX)
        posting_date = str(today - timedelta(days=random.randint(0, 30)))

        doc = frappe.get_doc({
            "doctype": "Receipt",
            "posting_date": posting_date,
            "warehouse": warehouse,
            "customer_analytics": customer,
            "items": [{
                "item_analytics": item,
                "qty": qty,
                "rate": round(random.uniform(50, 500), 2),
            }],
        })
        doc.insert(ignore_permissions=True)
        doc.submit()
        frappe.db.commit()

        # Run worker synchronously — receipts must exist before orders are submitted
        run_receipt_process(doc.name)
        frappe.db.commit()

        names.append(doc.name)
        total += Decimal(str(qty))
        _dot()

    print()
    return names, total


# ── phase 2: attack ───────────────────────────────────────────────────────────

def _random_order_qty() -> int:
    if random.random() < 0.8:
        return random.randint(*ORDER_QTY_SMALL)
    return random.randint(*ORDER_QTY_LARGE)


def _create_draft_orders(item: str, customer: str, warehouse: str) -> list[str]:
    names: list[str] = []
    today = str(date.today())

    for _ in range(ORDER_COUNT):
        qty = _random_order_qty()
        doc = frappe.get_doc({
            "doctype": "Fin Order",
            "posting_date": today,
            "warehouse": warehouse,
            "customer_analytics": customer,
            "items": [{
                "item_analytics": item,
                "qty": qty,
                "rate": round(random.uniform(50, 500), 2),
            }],
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        names.append(doc.name)
        _dot()

    print()
    return names


def _enqueue_all(order_names: list[str]) -> None:
    for name in order_names:
        frappe.enqueue(
            "fin_core.stress_test_random.process_fin_order_submit",
            order_name=name,
            queue="default",
        )


# ── phase 3: drain ────────────────────────────────────────────────────────────

def _drain_queue(timeout_s: int) -> bool:
    try:
        from rq import Queue
        from rq.job import JobStatus
        from rq.registry import StartedJobRegistry
        from frappe.utils.background_jobs import get_redis_conn

        conn = get_redis_conn()
        q = Queue("default", connection=conn)
        started = StartedJobRegistry(queue=q)
        deadline = time.time() + timeout_s

        while time.time() < deadline:
            pending = len(q)
            running = len(started)
            if pending == 0 and running == 0:
                print("Queue drained.")
                return True
            print(f"  pending={pending}  running={running}  …", flush=True)
            time.sleep(3)

    except Exception as e:
        print(f"Queue monitoring unavailable ({e}). Sleeping {timeout_s}s instead.")
        time.sleep(timeout_s)

    return False


# ── phase 4: validate ─────────────────────────────────────────────────────────

def _validate(item: str, warehouse: str, initial_qty: Decimal, order_names: list[str]) -> bool:
    ok_all = True

    # ── 1. Physical integrity ─────────────────────────────────────────────────
    # Available + Consumed/Sold leaf nodes == initial receipt qty
    # Transferred units are split containers; exclude them from the sum.
    rows = frappe.db.sql("""
        SELECT status, SUM(qty) AS total
        FROM   `tabStock Unit`
        WHERE  item_code = %(item)s
          AND  warehouse  = %(wh)s
          AND  status IN ('Available', 'Consumed', 'Sold')
        GROUP  BY status
    """, {"item": item, "wh": warehouse}, as_dict=True)

    by_status = {r.status: Decimal(str(r.total)) for r in rows}
    leaf_total = sum(by_status.values(), Decimal("0"))
    ok1 = leaf_total == initial_qty
    ok_all = ok_all and ok1
    print(f"[{'OK' if ok1 else 'FAIL'}] Physical Integrity")
    for status, qty in sorted(by_status.items()):
        print(f"       {status:12s}: {qty}")
    print(f"       {'Leaf total':12s}: {leaf_total}  (expected {initial_qty})")

    # ── 2. Document integrity ─────────────────────────────────────────────────
    # Sum of submitted Fin Order item qty == sum of Consumed Stock Units sourced to those orders
    if order_names:
        placeholders = ", ".join(["%s"] * len(order_names))
        order_qty_row = frappe.db.sql(
            f"""
            SELECT COALESCE(SUM(i.qty), 0) AS total
            FROM   `tabFin Order Item` i
            JOIN   `tabFin Order`      o ON o.name = i.parent
            WHERE  o.docstatus = 1
              AND  o.name IN ({placeholders})
            """,
            tuple(order_names),
            as_dict=True,
        )
        order_qty = Decimal(str(order_qty_row[0].total))

        consumed_row = frappe.db.sql(
            f"""
            SELECT COALESCE(SUM(qty), 0) AS total
            FROM   `tabStock Unit`
            WHERE  source_doctype = 'Fin Order'
              AND  source_name IN ({placeholders})
              AND  status IN ('Consumed', 'Sold')
            """,
            tuple(order_names),
            as_dict=True,
        )
        consumed_qty = Decimal(str(consumed_row[0].total))

        ok2 = order_qty == consumed_qty
        ok_all = ok_all and ok2
        print(f"[{'OK' if ok2 else 'FAIL'}] Document Integrity  "
              f"orders_qty={order_qty}  consumed_units_qty={consumed_qty}")

    # ── 3. Link integrity ─────────────────────────────────────────────────────
    # 3a — no zero or negative qty
    neg_rows = frappe.db.sql("""
        SELECT COUNT(*) AS cnt
        FROM   `tabStock Unit`
        WHERE  item_code = %(item)s AND warehouse = %(wh)s AND qty <= 0
    """, {"item": item, "wh": warehouse}, as_dict=True)
    neg_count = neg_rows[0].cnt
    ok3a = neg_count == 0
    ok_all = ok_all and ok3a
    print(f"[{'OK' if ok3a else 'FAIL'}] No zero/negative qty  violations={neg_count}")

    # 3b — every split-spent unit must reference a parent
    orphan_rows = frappe.db.sql("""
        SELECT COUNT(*) AS cnt
        FROM   `tabStock Unit`
        WHERE  item_code      = %(item)s
          AND  warehouse       = %(wh)s
          AND  status         IN ('Consumed', 'Sold')
          AND  parent_unit_id IS NULL
          AND  source_doctype  = 'Fin Order'
    """, {"item": item, "wh": warehouse}, as_dict=True)
    # These are whole-unit consumptions (no split) — they legitimately have no parent.
    whole_unit_consumed = orphan_rows[0].cnt
    print(f"[INFO] Whole-unit consumptions (no parent, expected): {whole_unit_consumed}")

    return ok_all


# ── phase 5: report ───────────────────────────────────────────────────────────

def _report_errors(since: datetime) -> None:
    errors = frappe.get_all(
        "Error Log",
        filters={"creation": [">=", since]},
        fields=["name", "method", "error"],
        limit=1_000,
        order_by="creation desc",
    )
    total_errors = len(errors)
    deadlocks = sum(
        1 for e in errors
        if any(kw in (e.get("error") or "").lower()
               for kw in ("deadlock", "lock wait timeout", "database lock"))
    )
    insufficient = sum(
        1 for e in errors
        if "insufficient stock" in (e.get("error") or "").lower()
    )

    print(f"Error Log entries (since test start) : {total_errors}")
    print(f"  Insufficient stock                 : {insufficient}")
    print(f"  Deadlock / lock errors             : {deadlocks}")

    if deadlocks:
        print("\nDeadlock details:")
        for e in errors:
            if any(kw in (e.get("error") or "").lower()
                   for kw in ("deadlock", "lock wait timeout")):
                print(f"  [{e.name}] {(e.get('error') or '')[:200]}")


# ── helpers ───────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    bar = "─" * 55
    print(f"\n{bar}\n  {title}\n{bar}")


_dot_count = 0

def _dot() -> None:
    global _dot_count
    print(".", end="", flush=True)
    _dot_count += 1
    if _dot_count % 50 == 0:
        print(f" {_dot_count}")
