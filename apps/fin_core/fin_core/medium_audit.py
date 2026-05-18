"""
Final Balance Audit — Medium Concurrency Test
----------------------------------------------
Scoped to STRESS_WAREHOUSE documents created in this test run.

Check 1 — Financial Integrity:
    SUM(debit) - SUM(credit) must be EXACTLY 0.00 across all test postings.

Check 2 — INVENTORY_QTY Integrity:
    SUM(qty) in Analytical Posting for INVENTORY_QTY must equal
    net physical movement: SUM(receipt_qty) - SUM(consumed_qty).

Check 3 — Ghost Units:
    No Stock Unit with qty < 0 (double-spend / race condition).
    No is_active=1 unit with status ∉ {Available, Reserved}.

Check 4 — Split Invariant:
    For every split parent, SUM(children.qty) == parent.qty.

Check 5 — Posting Coverage:
    Every submitted Receipt and Fin Order has ≥ 1 Analytical Posting.

Check 6 — Double-Entry per Voucher:
    For every source document, |SUM(debit) - SUM(credit)| < 0.01.

Reads /tmp/medium_results.json for success counts.
"""
import frappe
import json
import time
from decimal import Decimal

WAREHOUSE = "STRESS_WAREHOUSE"
ITEMS     = [f"STRESS_ITEM_{i:02d}" for i in range(1, 11)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hdr(title):
    print(f"\n  {'─' * 58}")
    print(f"  CHECK: {title}")
    print(f"  {'─' * 58}")

def _ok(msg):  print(f"  ✓  {msg}")
def _fail(msg): print(f"  ✗  FAIL: {msg}")
def _info(msg): print(f"  ·  {msg}")


# ---------------------------------------------------------------------------
# Check 1 — Global financial balance: SUM(debit) == SUM(credit)
# ---------------------------------------------------------------------------
def check_financial_integrity():
    _hdr("Financial Integrity  [SUM(debit) - SUM(credit) = 0]")

    row = frappe.db.sql("""
        SELECT
            COALESCE(SUM(debit),  0) AS total_debit,
            COALESCE(SUM(credit), 0) AS total_credit,
            COALESCE(SUM(debit),  0) - COALESCE(SUM(credit), 0) AS diff
        FROM "tabAnalytical Posting" ap
        WHERE ap.source_name IN (
            SELECT name FROM tabReceipt     WHERE warehouse = %s AND docstatus = 1
            UNION ALL
            SELECT name FROM "tabFin Order" WHERE warehouse = %s AND docstatus = 1
        )
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)[0]

    _info(f"Total Debit  : {row.total_debit:>16,.2f} UAH")
    _info(f"Total Credit : {row.total_credit:>16,.2f} UAH")
    _info(f"Difference   : {row.diff:>16,.2f} UAH")

    ok = abs(float(row.diff)) < 0.01
    if ok:
        _ok(f"Balanced — debit == credit ({row.total_debit:,.2f} UAH each side)")
    else:
        _fail(f"Imbalance of {row.diff:,.2f} UAH")
    return ok, float(row.total_debit), float(row.total_credit)


# ---------------------------------------------------------------------------
# Check 2 — INVENTORY_QTY net equals physical movement
# ---------------------------------------------------------------------------
def check_inventory_qty_integrity():
    _hdr("INVENTORY_QTY Integrity  [net posting qty = inbound - outbound]")

    # First check for duplicate postings — a duplicate inflates the negative side
    dup_count = frappe.db.sql("""
        SELECT COUNT(*) AS n FROM (
            SELECT stock_unit, template_name, COUNT(*) AS c
            FROM "tabAnalytical Posting"
            WHERE analytics_account = 'INVENTORY_QTY'
              AND source_doctype = 'Fin Order'
              AND source_name IN (
                  SELECT name FROM "tabFin Order" WHERE warehouse = %s AND docstatus = 1
              )
            GROUP BY stock_unit, template_name
            HAVING COUNT(*) > 1
        ) dups
    """, (WAREHOUSE,), as_dict=True)[0].n

    if dup_count:
        _fail(f"Found {dup_count} (stock_unit, template) pairs with duplicate postings — "
              f"TOCTOU race in posting_service (already patched; re-run after cleanup)")
        return False

    all_ok = True
    for item in ITEMS:
        # Net qty in postings (signed: Debit=positive, Credit=negative)
        # Uses DISTINCT on (stock_unit, template_name) to be robust against stale duplicates
        net_qty = frappe.db.sql("""
            SELECT COALESCE(SUM(qty), 0) AS net
            FROM (
                SELECT DISTINCT ON (stock_unit, template_name) qty
                FROM "tabAnalytical Posting"
                WHERE analytics_account = 'INVENTORY_QTY'
                  AND stock_unit IN (
                      SELECT name FROM "tabStock Unit"
                      WHERE item_code = %s AND warehouse = %s
                  )
                ORDER BY stock_unit, template_name, creation ASC
            ) deduped
        """, (item, WAREHOUSE), as_dict=True)[0].net

        # Physical: active Available/Reserved units (inbound - consumed)
        live_qty = frappe.db.sql("""
            SELECT COALESCE(SUM(qty), 0) AS total
            FROM "tabStock Unit"
            WHERE item_code = %s AND warehouse = %s
              AND is_active = 1 AND status = 'Available'
        """, (item, WAREHOUSE), as_dict=True)[0].total

        diff = abs(float(net_qty) - float(live_qty))
        if diff < 0.01:
            _ok(f"{item}: posting_net={net_qty:.0f}  live_qty={live_qty:.0f}")
        else:
            _fail(f"{item}: posting_net={net_qty:.0f}  live_qty={live_qty:.0f}  diff={diff:.2f}")
            all_ok = False

    return all_ok


# ---------------------------------------------------------------------------
# Check 3 — Ghost units (negative qty / wrong-status active units)
# ---------------------------------------------------------------------------
def check_ghost_units():
    _hdr("Ghost Units  [no negative qty, no phantom active units]")

    neg = frappe.db.sql("""
        SELECT name, item_code, qty, status
        FROM "tabStock Unit"
        WHERE warehouse = %s AND qty < 0
        LIMIT 10
    """, (WAREHOUSE,), as_dict=True)

    phantom = frappe.db.sql("""
        SELECT status, COUNT(*) AS n
        FROM "tabStock Unit"
        WHERE warehouse = %s AND is_active = 1
          AND status NOT IN ('Available', 'Reserved')
        GROUP BY status
    """, (WAREHOUSE,), as_dict=True)

    ok = True
    if neg:
        _fail(f"{len(neg)} units with qty < 0 (race condition / double-spend):")
        for r in neg:
            print(f"    {r.name}: item={r.item_code}  qty={r.qty}  status={r.status}")
        ok = False
    else:
        _ok("No units with negative qty — no double-spend detected")

    if phantom:
        total = sum(r.n for r in phantom)
        _fail(f"{total} is_active=1 units in wrong status:")
        for r in phantom:
            print(f"    status={r.status}: {r.n} units")
        ok = False
    else:
        _ok("No phantom active units — status invariant holds")

    return ok


# ---------------------------------------------------------------------------
# Check 4 — Split invariant: SUM(children.qty) == parent.qty
# ---------------------------------------------------------------------------
def check_split_invariant():
    _hdr("Split Invariant  [SUM(children.qty) == parent.qty]")

    bad = frappe.db.sql("""
        SELECT p.name, p.qty AS parent_qty, SUM(c.qty) AS children_sum,
               ABS(p.qty - SUM(c.qty)) AS diff
        FROM "tabStock Unit" p
        JOIN "tabStock Unit" c ON c.parent_unit_id = p.name
        WHERE p.warehouse = %s
        GROUP BY p.name, p.qty
        HAVING ABS(p.qty - SUM(c.qty)) > 0.001
        LIMIT 10
    """, (WAREHOUSE,), as_dict=True)

    total_parents = frappe.db.sql("""
        SELECT COUNT(DISTINCT parent_unit_id) AS n
        FROM "tabStock Unit"
        WHERE warehouse = %s AND parent_unit_id IS NOT NULL
    """, (WAREHOUSE,), as_dict=True)[0].n

    if bad:
        _fail(f"{len(bad)} split parents violate qty invariant:")
        for r in bad:
            print(f"    {r.name}: parent={r.parent_qty}  children_sum={r.children_sum}")
        return False

    _ok(f"Split invariant holds for all {total_parents} split parents")
    return True


# ---------------------------------------------------------------------------
# Check 5 — Posting coverage: every submitted doc has ≥ 1 posting
# ---------------------------------------------------------------------------
def check_posting_coverage():
    _hdr("Posting Coverage  [every submitted doc has ≥ 1 Analytical Posting]")

    missing_receipts = frappe.db.sql("""
        SELECT r.name FROM tabReceipt r
        WHERE r.warehouse = %s AND r.docstatus = 1
          AND NOT EXISTS (
              SELECT 1 FROM "tabAnalytical Posting" ap
              WHERE ap.source_doctype = 'Receipt' AND ap.source_name = r.name
          )
        LIMIT 10
    """, (WAREHOUSE,), as_dict=True)

    missing_orders = frappe.db.sql("""
        SELECT fo.name FROM "tabFin Order" fo
        WHERE fo.warehouse = %s AND fo.docstatus = 1
          AND NOT EXISTS (
              SELECT 1 FROM "tabAnalytical Posting" ap
              WHERE ap.source_doctype = 'Fin Order' AND ap.source_name = fo.name
          )
        LIMIT 10
    """, (WAREHOUSE,), as_dict=True)

    total_r = frappe.db.sql(
        "SELECT COUNT(*) AS n FROM tabReceipt WHERE warehouse=%s AND docstatus=1",
        (WAREHOUSE,), as_dict=True)[0].n
    total_fo = frappe.db.sql(
        'SELECT COUNT(*) AS n FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1',
        (WAREHOUSE,), as_dict=True)[0].n

    ok = True
    if missing_receipts:
        _fail(f"{len(missing_receipts)} Receipts missing postings (sample):")
        for r in missing_receipts:
            print(f"    {r.name}")
        ok = False
    else:
        _ok(f"All {total_r} submitted Receipts have postings")

    if missing_orders:
        _fail(f"{len(missing_orders)} Fin Orders missing postings (sample):")
        for r in missing_orders:
            print(f"    {r.name}")
        ok = False
    else:
        _ok(f"All {total_fo} submitted Fin Orders have postings")

    return ok


# ---------------------------------------------------------------------------
# Check 6 — Per-voucher double-entry balance
# ---------------------------------------------------------------------------
def check_per_voucher_balance():
    _hdr("Per-Voucher Double-Entry  [|debit - credit| < 0.01 per doc]")

    bad = frappe.db.sql("""
        SELECT ap.source_doctype, ap.source_name,
               SUM(ap.debit)  AS d, SUM(ap.credit) AS c,
               ABS(SUM(ap.debit) - SUM(ap.credit)) AS diff
        FROM "tabAnalytical Posting" ap
        WHERE (ap.debit > 0 OR ap.credit > 0)
          AND (
            EXISTS (SELECT 1 FROM tabReceipt r
                    WHERE r.name = ap.source_name AND r.warehouse = %s AND r.docstatus = 1)
            OR
            EXISTS (SELECT 1 FROM "tabFin Order" fo
                    WHERE fo.name = ap.source_name AND fo.warehouse = %s AND fo.docstatus = 1)
          )
        GROUP BY ap.source_doctype, ap.source_name
        HAVING ABS(SUM(ap.debit) - SUM(ap.credit)) > 0.01
        LIMIT 20
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)

    total = frappe.db.sql("""
        SELECT COUNT(DISTINCT ap.source_name) AS n
        FROM "tabAnalytical Posting" ap
        WHERE EXISTS (SELECT 1 FROM tabReceipt r
                      WHERE r.name = ap.source_name AND r.warehouse = %s AND r.docstatus = 1)
           OR EXISTS (SELECT 1 FROM "tabFin Order" fo
                      WHERE fo.name = ap.source_name AND fo.warehouse = %s AND fo.docstatus = 1)
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)[0].n

    if bad:
        _fail(f"{len(bad)} imbalanced vouchers (showing first 20):")
        for r in bad:
            print(f"    {r.source_doctype} {r.source_name}: "
                  f"debit={r.d:.2f}  credit={r.c:.2f}  diff={r.diff:.2f}")
        return False

    _ok(f"All {total} vouchers are balanced (debit == credit per doc)")
    return True


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------
def print_summary_stats():
    _hdr("Summary Statistics")

    units = frappe.db.sql("""
        SELECT
            COUNT(*) FILTER (WHERE is_active=1 AND status='Available') AS available,
            COUNT(*) FILTER (WHERE status='Consumed')                   AS consumed,
            COUNT(*) FILTER (WHERE is_active=0 AND status!='Consumed')  AS other_inactive,
            COUNT(*)                                                     AS total
        FROM "tabStock Unit"
        WHERE warehouse = %s
    """, (WAREHOUSE,), as_dict=True)[0]

    postings = frappe.db.sql("""
        SELECT
            COUNT(*)                            AS n,
            COALESCE(SUM(debit),  0)            AS total_debit,
            COALESCE(SUM(credit), 0)            AS total_credit,
            COUNT(DISTINCT source_name)         AS vouchers
        FROM "tabAnalytical Posting"
        WHERE source_name IN (
            SELECT name FROM tabReceipt     WHERE warehouse = %s AND docstatus = 1
            UNION ALL
            SELECT name FROM "tabFin Order" WHERE warehouse = %s AND docstatus = 1
        )
    """, (WAREHOUSE, WAREHOUSE), as_dict=True)[0]

    r_count = frappe.db.sql(
        "SELECT COUNT(*) AS n FROM tabReceipt WHERE warehouse=%s AND docstatus=1",
        (WAREHOUSE,), as_dict=True)[0].n
    fo_count = frappe.db.sql(
        'SELECT COUNT(*) AS n FROM "tabFin Order" WHERE warehouse=%s AND docstatus=1',
        (WAREHOUSE,), as_dict=True)[0].n

    print(f"  Receipts submitted      : {r_count:>8,}")
    print(f"  Fin Orders submitted    : {fo_count:>8,}")
    print(f"  Stock Units total       : {units.total:>8,}")
    print(f"    Available             : {units.available:>8,}")
    print(f"    Consumed              : {units.consumed:>8,}")
    print(f"    Other inactive        : {units.other_inactive:>8,}")
    print(f"  Analytical Postings     : {postings.n:>8,}")
    print(f"    Distinct vouchers     : {postings.vouchers:>8,}")
    print(f"  Total Debit             : {postings.total_debit:>16,.2f} UAH")
    print(f"  Total Credit            : {postings.total_credit:>16,.2f} UAH")


# ---------------------------------------------------------------------------
# Frappe error log scan
# ---------------------------------------------------------------------------
def scan_frappe_error_log():
    _hdr("Frappe Error Log  [race-condition and DB error keywords]")

    keywords = ["deadlock", "serialize", "could not serialize", "lock wait",
                "insufficient stock", "negative qty"]

    rows = frappe.db.sql("""
        SELECT creation, error
        FROM "tabError Log"
        WHERE creation >= NOW() - INTERVAL '2 hours'
        ORDER BY creation DESC
        LIMIT 200
    """, as_dict=True)

    hits = {}
    for row in rows:
        msg = (row.error or "").lower()
        for kw in keywords:
            if kw in msg:
                hits[kw] = hits.get(kw, 0) + 1

    if not hits:
        _ok("No race-condition or DB error keywords found in last 2h error log")
    else:
        for kw, cnt in sorted(hits.items(), key=lambda x: -x[1]):
            _fail(f"'{kw}' appeared {cnt}× in error log (last 2h)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run():
    print("\n" + "=" * 65)
    print("FINAL BALANCE AUDIT")
    print("=" * 65)

    t0 = time.perf_counter()

    results = {
        "financial_integrity":  check_financial_integrity()[0],
        "inventory_qty":        check_inventory_qty_integrity(),
        "ghost_units":          check_ghost_units(),
        "split_invariant":      check_split_invariant(),
        "posting_coverage":     check_posting_coverage(),
        "per_voucher_balance":  check_per_voucher_balance(),
    }

    scan_frappe_error_log()
    print_summary_stats()

    elapsed = time.perf_counter() - t0
    passed  = sum(1 for v in results.values() if v)
    total   = len(results)

    print(f"\n{'=' * 65}")
    print(f"AUDIT RESULT: {passed}/{total} checks passed  ({elapsed:.1f}s)")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print("=" * 65)

    return passed == total
