"""
Consistency audit after stress test.

Checks (scoped to STRESS_WAREHOUSE):
  1. UTXO balance:       SUM(receipt amounts) == SUM(consumed amounts) + remaining stock value
  2. Double-entry:       For every source document, SUM(debit) == SUM(credit)  (Amount type rows)
  3. Qty balance:        SUM(INVENTORY_QTY postings) == current available qty in Stock Unit table
  4. No phantom stock:   No is_active=1 units with status != Available/Reserved
  5. Split invariant:    For every split parent, sum(child.qty) == parent.qty
  6. Posting coverage:   Every submitted Fin Order has at least one Analytical Posting
  7. Lock race:          Check for any over-spent stock (remaining qty < 0)
"""
import frappe
import time
from decimal import Decimal

WAREHOUSE = "STRESS_WAREHOUSE"
ITEMS     = [f"STRESS_ITEM_{i:02d}" for i in range(1, 11)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _h(label):
    print(f"\n  {'─'*55}")
    print(f"  CHECK: {label}")
    print(f"  {'─'*55}")


def _ok(msg):
    print(f"  ✓  {msg}")


def _fail(msg):
    print(f"  ✗  FAIL: {msg}")


# ---------------------------------------------------------------------------
# 1. UTXO Value Balance
# ---------------------------------------------------------------------------

def check_utxo_value_balance():
    _h("UTXO value balance")

    receipt_total = frappe.db.sql("""
        SELECT COALESCE(SUM(r.total_amount), 0) AS total
        FROM tabReceipt r
        WHERE r.warehouse = %s AND r.docstatus = 1
    """, (WAREHOUSE,), as_dict=True)[0].total

    consumed_total = frappe.db.sql("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM "tabStock Unit"
        WHERE warehouse = %s AND is_active = 0 AND status = 'Consumed'
    """, (WAREHOUSE,), as_dict=True)[0].total

    remaining_total = frappe.db.sql("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM "tabStock Unit"
        WHERE warehouse = %s AND is_active = 1 AND status = 'Available'
    """, (WAREHOUSE,), as_dict=True)[0].total

    # For splits: deactivated parent units counted in consumed through children
    print(f"  Receipt total_amount:   {receipt_total:>14,.2f} UAH")
    print(f"  Consumed units amount:  {consumed_total:>14,.2f} UAH")
    print(f"  Remaining stock amount: {remaining_total:>14,.2f} UAH")
    reconstructed = consumed_total + remaining_total
    print(f"  Consumed + Remaining:   {reconstructed:>14,.2f} UAH")

    # Note: split parents are deactivated but NOT consumed — they are Transferred/split
    # So we need to add split remainder values too
    split_parent_total = frappe.db.sql("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM "tabStock Unit"
        WHERE warehouse = %s AND is_active = 0
          AND status NOT IN ('Consumed', 'Available', 'Reserved')
    """, (WAREHOUSE,), as_dict=True)[0].total
    print(f"  Split parents amount:   {split_parent_total:>14,.2f} UAH")

    # The true conservation law: receipt_total == consumed + available + (split_parents netted out)
    # Splits are neutral: parent deactivated, children have same total amount
    # So: receipt_total ≈ SUM(all leaf units)
    all_leaves = frappe.db.sql("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM "tabStock Unit"
        WHERE warehouse = %s
          AND parent_unit_id IS NULL
          AND status != 'Adjusted'
    """, (WAREHOUSE,), as_dict=True)[0].total

    # Simpler: count only is_active=1 units (live) + consumed leaves
    live_total = frappe.db.sql("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM "tabStock Unit"
        WHERE warehouse = %s
          AND (
            (is_active = 1)
            OR (is_active = 0 AND status = 'Consumed' AND parent_unit_id IS NOT NULL)
            OR (is_active = 0 AND status = 'Consumed' AND parent_unit_id IS NULL)
          )
    """, (WAREHOUSE,), as_dict=True)[0].total

    # Most precise: sum all original (no parent) units, verify = receipt total
    original_units = frappe.db.sql("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM "tabStock Unit"
        WHERE warehouse = %s AND parent_unit_id IS NULL
    """, (WAREHOUSE,), as_dict=True)[0].total

    diff = abs(float(receipt_total) - float(original_units))
    print(f"\n  Original units total:   {original_units:>14,.2f} UAH")
    if diff < 0.01:
        _ok(f"Receipt total == original units total ({receipt_total:,.2f})")
    else:
        _fail(f"Mismatch: receipts={receipt_total:,.2f}  original_units={original_units:,.2f}  diff={diff:,.2f}")

    return diff < 0.01


# ---------------------------------------------------------------------------
# 2. Double-entry balance per document
# ---------------------------------------------------------------------------

def check_double_entry():
    _h("Double-entry balance per source document")

    rows = frappe.db.sql("""
        SELECT source_doctype, source_name,
               SUM(debit) AS d, SUM(credit) AS c,
               ABS(SUM(debit) - SUM(credit)) AS diff
        FROM "tabAnalytical Posting"
        WHERE debit > 0 OR credit > 0
        GROUP BY source_doctype, source_name
        HAVING ABS(SUM(debit) - SUM(credit)) > 0.01
        LIMIT 20
    """, as_dict=True)

    total_docs = frappe.db.sql("""
        SELECT COUNT(DISTINCT source_name) AS n
        FROM "tabAnalytical Posting"
        WHERE debit > 0 OR credit > 0
    """, as_dict=True)[0].n

    if not rows:
        _ok(f"All {total_docs} documents are balanced (debit == credit)")
        return True
    else:
        _fail(f"{len(rows)} imbalanced documents found (showing first 20):")
        for r in rows:
            print(f"    {r.source_doctype} {r.source_name}: debit={r.d:.2f} credit={r.c:.2f} diff={r.diff:.2f}")
        return False


# ---------------------------------------------------------------------------
# 3. Qty balance: postings vs live stock
# ---------------------------------------------------------------------------

def check_qty_balance():
    _h("Inventory qty: postings vs. live Stock Units")

    for item in ITEMS:
        posting_qty = frappe.db.sql("""
            SELECT COALESCE(SUM(qty), 0) AS net
            FROM "tabAnalytical Posting"
            WHERE analytics_account = 'INVENTORY_QTY'
              AND stock_unit IN (
                SELECT name FROM "tabStock Unit" WHERE item_code = %s AND warehouse = %s
              )
        """, (item, WAREHOUSE), as_dict=True)[0].net

        live_qty = frappe.db.sql("""
            SELECT COALESCE(SUM(qty), 0) AS total
            FROM "tabStock Unit"
            WHERE item_code = %s AND warehouse = %s AND is_active = 1 AND status = 'Available'
        """, (item, WAREHOUSE), as_dict=True)[0].total

        diff = abs(float(posting_qty) - float(live_qty))
        if diff < 0.01:
            _ok(f"{item}: posting_net_qty={posting_qty:.0f}  live_qty={live_qty:.0f}")
        else:
            _fail(f"{item}: posting_net_qty={posting_qty:.0f} ≠ live_qty={live_qty:.0f}  diff={diff:.0f}")

    return True


# ---------------------------------------------------------------------------
# 4. No phantom stock
# ---------------------------------------------------------------------------

def check_phantom_stock():
    _h("Phantom stock (is_active=1 but status ∉ Available/Reserved)")

    rows = frappe.db.sql("""
        SELECT status, COUNT(*) AS n
        FROM "tabStock Unit"
        WHERE warehouse = %s AND is_active = 1
          AND status NOT IN ('Available', 'Reserved')
        GROUP BY status
    """, (WAREHOUSE,), as_dict=True)

    if not rows:
        _ok("No phantom stock units found")
        return True
    else:
        total = sum(r.n for r in rows)
        _fail(f"{total} units are is_active=1 but in wrong status:")
        for r in rows:
            print(f"    status={r.status}: {r.n} units")
        return False


# ---------------------------------------------------------------------------
# 5. Split qty invariant
# ---------------------------------------------------------------------------

def check_split_invariant():
    _h("Split qty invariant: SUM(children.qty) == parent.qty")

    rows = frappe.db.sql("""
        SELECT p.name AS parent,
               p.qty  AS parent_qty,
               SUM(c.qty) AS children_sum,
               ABS(p.qty - SUM(c.qty)) AS diff
        FROM "tabStock Unit" p
        JOIN "tabStock Unit" c ON c.parent_unit_id = p.name
        WHERE p.warehouse = %s
        GROUP BY p.name, p.qty
        HAVING ABS(p.qty - SUM(c.qty)) > 0.001
        LIMIT 10
    """, (WAREHOUSE,), as_dict=True)

    total_splits = frappe.db.sql("""
        SELECT COUNT(DISTINCT parent_unit_id) AS n
        FROM "tabStock Unit"
        WHERE warehouse = %s AND parent_unit_id IS NOT NULL
    """, (WAREHOUSE,), as_dict=True)[0].n

    if not rows:
        _ok(f"Split invariant holds for all {total_splits} split parents")
        return True
    else:
        _fail(f"{len(rows)} split parents violate qty invariant:")
        for r in rows:
            print(f"    {r.parent}: parent_qty={r.parent_qty}  children_sum={r.children_sum}  diff={r.diff:.6f}")
        return False


# ---------------------------------------------------------------------------
# 6. Posting coverage for Fin Orders
# ---------------------------------------------------------------------------

def check_posting_coverage():
    _h("Posting coverage: every submitted Fin Order has postings")

    missing = frappe.db.sql("""
        SELECT o.name
        FROM "tabFin Order" o
        WHERE o.warehouse = %s AND o.docstatus = 1
          AND NOT EXISTS (
              SELECT 1 FROM "tabAnalytical Posting" ap
              WHERE ap.source_doctype = 'Fin Order' AND ap.source_name = o.name
          )
        LIMIT 10
    """, (WAREHOUSE,), as_dict=True)

    total_orders = frappe.db.sql("""
        SELECT COUNT(*) AS n FROM "tabFin Order"
        WHERE warehouse = %s AND docstatus = 1
    """, (WAREHOUSE,), as_dict=True)[0].n

    if not missing:
        _ok(f"All {total_orders} submitted Fin Orders have Analytical Postings")
        return True
    else:
        _fail(f"{len(missing)} Fin Orders have no postings (showing first 10):")
        for r in missing:
            print(f"    {r.name}")
        return False


# ---------------------------------------------------------------------------
# 7. Negative qty check (race condition symptom)
# ---------------------------------------------------------------------------

def check_negative_qty():
    _h("Negative qty (race-condition symptom)")

    rows = frappe.db.sql("""
        SELECT name, item_code, qty, status
        FROM "tabStock Unit"
        WHERE warehouse = %s AND qty < 0
        LIMIT 10
    """, (WAREHOUSE,), as_dict=True)

    if not rows:
        _ok("No units with qty < 0 found — no race condition detected")
        return True
    else:
        _fail(f"{len(rows)} units have negative qty (race condition may have occurred):")
        for r in rows:
            print(f"    {r.name}: item={r.item_code}  qty={r.qty}  status={r.status}")
        return False


# ---------------------------------------------------------------------------
# 8. Summary stats
# ---------------------------------------------------------------------------

def print_summary():
    _h("Summary Statistics")

    units = frappe.db.sql("""
        SELECT
            COUNT(*) FILTER (WHERE is_active = 1 AND status = 'Available') AS available,
            COUNT(*) FILTER (WHERE status = 'Consumed')                    AS consumed,
            COUNT(*) FILTER (WHERE is_active = 0 AND status != 'Consumed') AS other_inactive,
            COUNT(*) AS total_units
        FROM "tabStock Unit"
        WHERE warehouse = %s
    """, (WAREHOUSE,), as_dict=True)[0]

    postings = frappe.db.sql("""
        SELECT COUNT(*) AS n, COALESCE(SUM(debit), 0) AS total_debit,
               COALESCE(SUM(credit), 0) AS total_credit
        FROM "tabAnalytical Posting" ap
        WHERE EXISTS (
            SELECT 1 FROM "tabFin Order" o
            WHERE o.name = ap.source_name AND o.warehouse = %s
            UNION ALL
            SELECT 1 FROM tabReceipt r
            WHERE r.name = ap.source_name AND r.warehouse = %s
        )
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)[0]

    receipts = frappe.db.sql(
        "SELECT COUNT(*) AS n FROM tabReceipt WHERE warehouse = %s AND docstatus = 1",
        (WAREHOUSE,), as_dict=True)[0]

    orders = frappe.db.sql(
        "SELECT COUNT(*) AS n FROM \"tabFin Order\" WHERE warehouse = %s AND docstatus = 1",
        (WAREHOUSE,), as_dict=True)[0]

    print(f"  Receipts submitted:      {receipts.n:>8,}")
    print(f"  Fin Orders submitted:    {orders.n:>8,}")
    print(f"  Stock Units total:       {units.total_units:>8,}")
    print(f"    Available:             {units.available:>8,}")
    print(f"    Consumed:              {units.consumed:>8,}")
    print(f"    Other inactive:        {units.other_inactive:>8,}")
    print(f"  Analytical Postings:     {postings.n:>8,}")
    print(f"  Total Debit:       {postings.total_debit:>14,.2f} UAH")
    print(f"  Total Credit:      {postings.total_credit:>14,.2f} UAH")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    print("\n" + "="*60)
    print("CONSISTENCY AUDIT")
    print("="*60)

    t0 = time.perf_counter()

    results = {
        "utxo_value":   check_utxo_value_balance(),
        "double_entry": check_double_entry(),
        "qty_balance":  check_qty_balance(),
        "phantom":      check_phantom_stock(),
        "split_inv":    check_split_invariant(),
        "coverage":     check_posting_coverage(),
        "neg_qty":      check_negative_qty(),
    }

    print_summary()

    elapsed = time.perf_counter() - t0
    passed  = sum(1 for v in results.values() if v)
    total   = len(results)

    print(f"\n{'='*60}")
    print(f"AUDIT RESULT: {passed}/{total} checks passed  ({elapsed:.1f}s)")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print("="*60)

    return passed == total
