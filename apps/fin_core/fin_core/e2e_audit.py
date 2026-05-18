"""
E2E Integrity Audit — MAIN_WAREHOUSE
======================================
Runs AFTER e2e_test.py completes and all workers have drained.

Check 1 — Posting Logic per Fin Order:
    Every submitted Fin Order must have:
    a) ≥1 "Amount" row  : (debit>0 OR credit>0) AND qty=0
    b) ≥1 "Quantity" row: qty≠0 AND debit=0 AND credit=0
    c) Zero "leak" rows : rows with BOTH money AND qty filled

Check 2 — Financial Balance per voucher (source_name):
    ABS(SUM(debit) - SUM(credit)) < 0.01 for every source document.

Check 3 — INVENTORY_QTY net matches physical stock:
    SUM(qty) WHERE analytics_account='INVENTORY_QTY'
    must equal the live available qty per item.

Check 4 — Ghost Units:
    No qty < 0, no is_active=1 with wrong status.

Check 5 — Split Invariant:
    SUM(children.qty) == parent.qty for every split.

Check 6 — Column Separation (no leak):
    Confirm Quantity rows carry zero debit AND zero credit across ALL postings.
"""
import frappe
import time

WAREHOUSE = "MAIN_WAREHOUSE"
ITEMS     = ["ITEM_STEEL_PIPE", "ITEM_WOOD_PLANK"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hdr(title):
    print(f"\n  {'─' * 60}")
    print(f"  CHECK: {title}")
    print(f"  {'─' * 60}")

def _ok(msg):   print(f"  ✓  {msg}")
def _fail(msg): print(f"  ✗  FAIL: {msg}")
def _info(msg): print(f"  ·  {msg}")


# ---------------------------------------------------------------------------
# Check 1 — Posting logic: Amount rows + Quantity rows + no leaks
# ---------------------------------------------------------------------------
def check_posting_logic():
    _hdr("Posting Logic per Fin Order  [Amount rows ∧ Quantity rows ∧ no leaks]")

    total_fo = frappe.db.sql("""
        SELECT COUNT(*) AS n FROM "tabFin Order"
        WHERE warehouse = %s AND docstatus = 1
    """, (WAREHOUSE,), as_dict=True)[0].n

    # a) Orders missing Amount rows entirely
    missing_amount = frappe.db.sql("""
        SELECT fo.name
        FROM "tabFin Order" fo
        WHERE fo.warehouse = %s AND fo.docstatus = 1
          AND NOT EXISTS (
              SELECT 1 FROM "tabAnalytical Posting" ap
              WHERE ap.source_name = fo.name
                AND (ap.debit > 0 OR ap.credit > 0)
                AND ap.qty = 0
          )
        LIMIT 10
    """, (WAREHOUSE,), as_dict=True)

    # b) Orders missing Quantity rows entirely
    missing_qty = frappe.db.sql("""
        SELECT fo.name
        FROM "tabFin Order" fo
        WHERE fo.warehouse = %s AND fo.docstatus = 1
          AND NOT EXISTS (
              SELECT 1 FROM "tabAnalytical Posting" ap
              WHERE ap.source_name = fo.name
                AND ap.qty <> 0
                AND ap.debit = 0
                AND ap.credit = 0
          )
        LIMIT 10
    """, (WAREHOUSE,), as_dict=True)

    # c) Leak rows: qty != 0 AND (debit > 0 OR credit > 0) in the same row
    leak_rows = frappe.db.sql("""
        SELECT ap.name, ap.source_name, ap.analytics_account,
               ap.debit, ap.credit, ap.qty
        FROM "tabAnalytical Posting" ap
        JOIN "tabFin Order" fo ON fo.name = ap.source_name
        WHERE fo.warehouse = %s AND fo.docstatus = 1
          AND ap.qty <> 0
          AND (ap.debit > 0 OR ap.credit > 0)
        LIMIT 10
    """, (WAREHOUSE,), as_dict=True)

    ok = True
    if missing_amount:
        _fail(f"{len(missing_amount)} Fin Orders have no Amount postings:")
        for r in missing_amount:
            print(f"    {r.name}")
        ok = False
    else:
        _ok(f"All {total_fo} Fin Orders have Amount (debit/credit) rows")

    if missing_qty:
        _fail(f"{len(missing_qty)} Fin Orders have no Quantity postings:")
        for r in missing_qty:
            print(f"    {r.name}")
        ok = False
    else:
        _ok(f"All {total_fo} Fin Orders have Quantity (qty) rows")

    if leak_rows:
        _fail(f"{len(leak_rows)} rows carry BOTH money AND qty (column separation leak):")
        for r in leak_rows:
            print(f"    {r.name}: acct={r.analytics_account} "
                  f"debit={r.debit} credit={r.credit} qty={r.qty}")
        ok = False
    else:
        _ok("No leak rows — Amount columns and Quantity column are fully separated")

    return ok


# ---------------------------------------------------------------------------
# Check 2 — Financial balance per voucher
# ---------------------------------------------------------------------------
def check_financial_balance():
    _hdr("Financial Balance per Voucher  [|SUM(debit) - SUM(credit)| < 0.01]")

    # Global totals first
    totals = frappe.db.sql("""
        SELECT COALESCE(SUM(ap.debit),0)  AS total_debit,
               COALESCE(SUM(ap.credit),0) AS total_credit,
               COALESCE(SUM(ap.debit),0) - COALESCE(SUM(ap.credit),0) AS diff
        FROM "tabAnalytical Posting" ap
        WHERE ap.source_name IN (
            SELECT name FROM tabReceipt     WHERE warehouse=%s AND docstatus=1
            UNION ALL
            SELECT name FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1
        )
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)[0]

    _info(f"Total Debit    : {totals.total_debit:>14,.2f} UAH")
    _info(f"Total Credit   : {totals.total_credit:>14,.2f} UAH")
    _info(f"Global diff    : {totals.diff:>14,.2f} UAH")

    # Per-voucher check
    imbalanced = frappe.db.sql("""
        SELECT ap.source_doctype, ap.source_name,
               SUM(ap.debit) AS d, SUM(ap.credit) AS c,
               ABS(SUM(ap.debit) - SUM(ap.credit)) AS diff
        FROM "tabAnalytical Posting" ap
        WHERE ap.source_name IN (
            SELECT name FROM tabReceipt     WHERE warehouse=%s AND docstatus=1
            UNION ALL
            SELECT name FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1
        )
        GROUP BY ap.source_doctype, ap.source_name
        HAVING ABS(SUM(ap.debit) - SUM(ap.credit)) > 0.01
        LIMIT 20
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)

    total_vouchers = frappe.db.sql("""
        SELECT COUNT(DISTINCT ap.source_name) AS n
        FROM "tabAnalytical Posting" ap
        WHERE ap.source_name IN (
            SELECT name FROM tabReceipt     WHERE warehouse=%s AND docstatus=1
            UNION ALL
            SELECT name FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1
        )
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)[0].n

    ok = abs(float(totals.diff)) < 0.01

    if imbalanced:
        _fail(f"{len(imbalanced)} imbalanced vouchers (showing up to 20):")
        for r in imbalanced:
            print(f"    {r.source_doctype} {r.source_name}: "
                  f"debit={r.d:.2f}  credit={r.c:.2f}  diff={r.diff:.4f}")
        ok = False
    else:
        _ok(f"All {total_vouchers} vouchers balanced — SUM(debit)==SUM(credit)")

    if abs(float(totals.diff)) < 0.01:
        _ok(f"Grand total balanced: {totals.total_debit:,.2f} UAH each side")
    else:
        _fail(f"Grand total imbalance: {totals.diff:,.4f} UAH")
        ok = False

    return ok


# ---------------------------------------------------------------------------
# Check 3 — INVENTORY_QTY net matches physical stock
# ---------------------------------------------------------------------------
def check_inventory_qty():
    _hdr("INVENTORY_QTY Net  [SUM(posting.qty) = live stock qty per item]")

    # Pre-check for duplicate postings (TOCTOU race symptom)
    dup_count = frappe.db.sql("""
        SELECT COUNT(*) AS n FROM (
            SELECT ap.stock_unit, ap.template_name, COUNT(*) AS c
            FROM "tabAnalytical Posting" ap
            WHERE ap.analytics_account = 'INVENTORY_QTY'
              AND ap.source_name IN (
                  SELECT name FROM tabReceipt     WHERE warehouse=%s AND docstatus=1
                  UNION ALL
                  SELECT name FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1
              )
            GROUP BY ap.stock_unit, ap.template_name
            HAVING COUNT(*) > 1
        ) dups
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)[0].n

    if dup_count:
        _fail(f"Found {dup_count} duplicate (stock_unit, template) posting pairs "
              f"— advisory-lock patch must be active in posting_service.py")
        return False

    all_ok = True
    for item in ITEMS:
        # Net qty from postings (signed: Debit=+, Credit=-)
        net = frappe.db.sql("""
            SELECT COALESCE(SUM(ap.qty), 0) AS net
            FROM "tabAnalytical Posting" ap
            WHERE ap.analytics_account = 'INVENTORY_QTY'
              AND ap.stock_unit IN (
                  SELECT name FROM "tabStock Unit"
                  WHERE item_code = %s AND warehouse = %s
              )
        """, (item, WAREHOUSE), as_dict=True)[0].net

        # Physical live qty
        live = frappe.db.sql("""
            SELECT COALESCE(SUM(qty), 0) AS total
            FROM "tabStock Unit"
            WHERE item_code = %s AND warehouse = %s
              AND is_active = 1 AND status = 'Available'
        """, (item, WAREHOUSE), as_dict=True)[0].total

        diff = abs(float(net) - float(live))
        if diff < 0.01:
            _ok(f"{item}: posting_net={float(net):.0f}  live_qty={float(live):.0f}")
        else:
            _fail(f"{item}: posting_net={float(net):.0f}  live_qty={float(live):.0f}  diff={diff:.2f}")
            all_ok = False

    # Receipt inflow vs Fin Order outflow breakdown
    inflow = frappe.db.sql("""
        SELECT COALESCE(SUM(ap.qty),0) AS total
        FROM "tabAnalytical Posting" ap
        WHERE ap.analytics_account = 'INVENTORY_QTY' AND ap.qty > 0
          AND ap.source_name IN (SELECT name FROM tabReceipt WHERE warehouse=%s AND docstatus=1)
    """, (WAREHOUSE,), as_dict=True)[0].total

    outflow = frappe.db.sql("""
        SELECT COALESCE(SUM(ap.qty),0) AS total
        FROM "tabAnalytical Posting" ap
        WHERE ap.analytics_account = 'INVENTORY_QTY' AND ap.qty < 0
          AND ap.source_name IN (SELECT name FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1)
    """, (WAREHOUSE,), as_dict=True)[0].total

    _info(f"Receipt inflow  (INVENTORY_QTY Debit) : +{float(inflow):.0f}")
    _info(f"FO outflow      (INVENTORY_QTY Credit): {float(outflow):.0f}")
    _info(f"Net             (should = live stock)  : {float(inflow)+float(outflow):.0f}")

    return all_ok


# ---------------------------------------------------------------------------
# Check 4 — Ghost units
# ---------------------------------------------------------------------------
def check_ghost_units():
    _hdr("Ghost Units  [no negative qty, no phantom active units]")

    neg = frappe.db.sql("""
        SELECT name, item_code, qty, status FROM "tabStock Unit"
        WHERE warehouse = %s AND qty < 0 LIMIT 5
    """, (WAREHOUSE,), as_dict=True)

    phantom = frappe.db.sql("""
        SELECT status, COUNT(*) AS n FROM "tabStock Unit"
        WHERE warehouse = %s AND is_active = 1
          AND status NOT IN ('Available', 'Reserved')
        GROUP BY status
    """, (WAREHOUSE,), as_dict=True)

    ok = True
    if neg:
        _fail(f"{len(neg)} units with qty < 0:")
        for r in neg:
            print(f"    {r.name}: {r.item_code}  qty={r.qty}  {r.status}")
        ok = False
    else:
        _ok("No negative-qty units — zero double-spend")

    if phantom:
        _fail(f"Phantom active units: {[(r.status, r.n) for r in phantom]}")
        ok = False
    else:
        _ok("No phantom active units — status invariant holds")

    return ok


# ---------------------------------------------------------------------------
# Check 5 — Split invariant
# ---------------------------------------------------------------------------
def check_split_invariant():
    _hdr("Split Invariant  [SUM(children.qty) == parent.qty]")

    bad = frappe.db.sql("""
        SELECT p.name, p.qty, SUM(c.qty) AS c_sum,
               ABS(p.qty - SUM(c.qty)) AS diff
        FROM "tabStock Unit" p
        JOIN "tabStock Unit" c ON c.parent_unit_id = p.name
        WHERE p.warehouse = %s
        GROUP BY p.name, p.qty
        HAVING ABS(p.qty - SUM(c.qty)) > 0.001
        LIMIT 5
    """, (WAREHOUSE,), as_dict=True)

    n_splits = frappe.db.sql("""
        SELECT COUNT(DISTINCT parent_unit_id) AS n FROM "tabStock Unit"
        WHERE warehouse = %s AND parent_unit_id IS NOT NULL
    """, (WAREHOUSE,), as_dict=True)[0].n

    if bad:
        _fail(f"{len(bad)} split parents violate qty invariant")
        for r in bad:
            print(f"    {r.name}: parent={r.qty}  children={r.c_sum}")
        return False

    _ok(f"Split invariant holds for all {n_splits} split parents")
    return True


# ---------------------------------------------------------------------------
# Check 6 — Column separation: Quantity rows have debit=0 AND credit=0
# ---------------------------------------------------------------------------
def check_column_separation():
    _hdr("Column Separation  [Quantity rows: debit=0 AND credit=0, no Amount in qty column]")

    # Any qty-type row that also has money — should be zero
    qty_with_money = frappe.db.sql("""
        SELECT COUNT(*) AS n
        FROM "tabAnalytical Posting" ap
        WHERE ap.source_name IN (
            SELECT name FROM tabReceipt     WHERE warehouse=%s AND docstatus=1
            UNION ALL
            SELECT name FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1
        )
          AND ap.qty <> 0
          AND (ap.debit <> 0 OR ap.credit <> 0)
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)[0].n

    # Any Amount row that also has qty
    amount_with_qty = frappe.db.sql("""
        SELECT COUNT(*) AS n
        FROM "tabAnalytical Posting" ap
        WHERE ap.source_name IN (
            SELECT name FROM tabReceipt     WHERE warehouse=%s AND docstatus=1
            UNION ALL
            SELECT name FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1
        )
          AND (ap.debit <> 0 OR ap.credit <> 0)
          AND ap.qty <> 0
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)[0].n

    # Stats: how many distinct row types
    row_types = frappe.db.sql("""
        SELECT
            COUNT(*) FILTER (WHERE qty = 0 AND (debit>0 OR credit>0)) AS amount_rows,
            COUNT(*) FILTER (WHERE qty <> 0 AND debit=0 AND credit=0) AS quantity_rows,
            COUNT(*) FILTER (WHERE qty <> 0 AND (debit>0 OR credit>0)) AS mixed_rows,
            COUNT(*) FILTER (WHERE qty = 0 AND debit=0 AND credit=0)  AS zero_rows
        FROM "tabAnalytical Posting" ap
        WHERE ap.source_name IN (
            SELECT name FROM tabReceipt     WHERE warehouse=%s AND docstatus=1
            UNION ALL
            SELECT name FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1
        )
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)[0]

    _info(f"Amount rows (debit/credit only): {row_types.amount_rows}")
    _info(f"Quantity rows (qty only)        : {row_types.quantity_rows}")
    _info(f"Mixed rows (both)               : {row_types.mixed_rows}")
    _info(f"Zero rows                       : {row_types.zero_rows}")

    ok = qty_with_money == 0 and amount_with_qty == 0

    if qty_with_money > 0:
        _fail(f"{qty_with_money} rows have qty≠0 AND debit/credit≠0 (Quantity leaked into Amount)")
    if amount_with_qty > 0:
        _fail(f"{amount_with_qty} rows have debit/credit≠0 AND qty≠0 (Amount leaked into Quantity)")
    if ok:
        _ok("Perfect column separation — Amount and Quantity never co-exist in the same row")

    return ok


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------
def print_summary():
    _hdr("Summary Statistics")

    r = frappe.db.sql(
        "SELECT COUNT(*) AS n FROM tabReceipt WHERE warehouse=%s AND docstatus=1",
        (WAREHOUSE,), as_dict=True)[0]
    fo = frappe.db.sql(
        'SELECT COUNT(*) AS n FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1',
        (WAREHOUSE,), as_dict=True)[0]

    su = frappe.db.sql("""
        SELECT
            COUNT(*) FILTER (WHERE is_active=1 AND status='Available') AS available,
            COUNT(*) FILTER (WHERE status='Consumed')                   AS consumed,
            COUNT(*) FILTER (WHERE is_active=0 AND status='Consumed' AND parent_unit_id IS NOT NULL) AS split_consumed,
            COUNT(*)                                                     AS total
        FROM "tabStock Unit" WHERE warehouse=%s
    """, (WAREHOUSE,), as_dict=True)[0]

    ap = frappe.db.sql("""
        SELECT COUNT(*) AS n,
               COUNT(*) FILTER (WHERE qty=0 AND (debit>0 OR credit>0)) AS amount_rows,
               COUNT(*) FILTER (WHERE qty<>0 AND debit=0 AND credit=0) AS qty_rows,
               COALESCE(SUM(debit),0) AS total_debit,
               COALESCE(SUM(credit),0) AS total_credit
        FROM "tabAnalytical Posting"
        WHERE source_name IN (
            SELECT name FROM tabReceipt     WHERE warehouse=%s AND docstatus=1
            UNION ALL
            SELECT name FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1
        )
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)[0]

    print(f"  Receipts submitted        : {r.n:>6,}")
    print(f"  Fin Orders submitted      : {fo.n:>6,}")
    print(f"  Stock Units total         : {su.total:>6,}")
    print(f"    Available               : {su.available:>6,}")
    print(f"    Consumed (all)          : {su.consumed:>6,}")
    print(f"    Split+consumed          : {su.split_consumed:>6,}")
    print(f"  Analytical Postings total : {ap.n:>6,}")
    print(f"    Amount rows             : {ap.amount_rows:>6,}")
    print(f"    Quantity rows           : {ap.qty_rows:>6,}")
    print(f"  Total Debit               : {ap.total_debit:>14,.2f} UAH")
    print(f"  Total Credit              : {ap.total_credit:>14,.2f} UAH")


# ---------------------------------------------------------------------------
# Error log scan
# ---------------------------------------------------------------------------
def scan_error_log():
    _hdr("Frappe Error Log  [last 30 min, race-condition keywords]")

    keywords = ["deadlock", "could not serialize", "lock wait",
                "insufficient stock", "double", "negative qty"]

    rows = frappe.db.sql("""
        SELECT creation, error FROM "tabError Log"
        WHERE creation >= NOW() - INTERVAL '30 minutes'
        ORDER BY creation DESC LIMIT 100
    """, as_dict=True)

    hits = {}
    for row in rows:
        msg = (row.error or "").lower()
        for kw in keywords:
            if kw in msg:
                hits[kw] = hits.get(kw, 0) + 1

    if not hits:
        _ok("No race-condition or DB error keywords in the last 30-minute error log")
    else:
        for kw, cnt in sorted(hits.items(), key=lambda x: -x[1]):
            _fail(f"'{kw}' appeared {cnt}× in error log (last 30 min)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run():
    print("\n" + "=" * 62)
    print("E2E INTEGRITY AUDIT — MAIN_WAREHOUSE")
    print("=" * 62)

    t0 = time.perf_counter()

    results = {
        "posting_logic":      check_posting_logic(),
        "financial_balance":  check_financial_balance(),
        "inventory_qty":      check_inventory_qty(),
        "ghost_units":        check_ghost_units(),
        "split_invariant":    check_split_invariant(),
        "column_separation":  check_column_separation(),
    }

    scan_error_log()
    print_summary()

    elapsed = time.perf_counter() - t0
    passed  = sum(1 for v in results.values() if v)
    total   = len(results)

    print(f"\n{'=' * 62}")
    print(f"AUDIT RESULT: {passed}/{total} checks passed  ({elapsed:.1f}s)")
    for name, ok in results.items():
        icon = "PASS" if ok else "FAIL"
        print(f"  {icon}  {name}")
    print("=" * 62)

    return passed == total
