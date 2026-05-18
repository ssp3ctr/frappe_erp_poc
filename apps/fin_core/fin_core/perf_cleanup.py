"""
Cleanup stress test data from STRESS_WAREHOUSE.
Deletes: Stock Units, Analytical Postings, Fin Orders, Receipts (all scoped to STRESS_WAREHOUSE).
Also resets Fin Order Stock Unit child rows.
"""
import frappe

WAREHOUSE = "STRESS_WAREHOUSE"


def run():
    print("\n=== Cleanup stress test data ===")

    # Analytical Postings linked to stress receipts or fin orders
    ap_del = frappe.db.sql("""
        DELETE FROM "tabAnalytical Posting"
        WHERE source_name IN (
            SELECT name FROM tabReceipt WHERE warehouse = %s
            UNION ALL
            SELECT name FROM "tabFin Order" WHERE warehouse = %s
        )
    """, (WAREHOUSE, WAREHOUSE))
    print(f"  Deleted Analytical Postings")

    # Fin Order Stock Unit child rows
    frappe.db.sql("""
        DELETE FROM "tabFin Order Stock Unit"
        WHERE parent IN (SELECT name FROM "tabFin Order" WHERE warehouse = %s)
    """, (WAREHOUSE,))
    print(f"  Deleted Fin Order Stock Unit rows")

    # Stock Units
    frappe.db.sql("""
        DELETE FROM "tabStock Unit" WHERE warehouse = %s
    """, (WAREHOUSE,))
    print(f"  Deleted Stock Units")

    # Fin Order items then Fin Orders
    frappe.db.sql("""
        DELETE FROM "tabFin Order Item"
        WHERE parent IN (SELECT name FROM "tabFin Order" WHERE warehouse = %s)
    """, (WAREHOUSE,))
    frappe.db.sql("""
        DELETE FROM "tabFin Order" WHERE warehouse = %s
    """, (WAREHOUSE,))
    print(f"  Deleted Fin Orders")

    # Receipt items then Receipts
    frappe.db.sql("""
        DELETE FROM "tabReceipt Item"
        WHERE parent IN (SELECT name FROM tabReceipt WHERE warehouse = %s)
    """, (WAREHOUSE,))
    frappe.db.sql("""
        DELETE FROM tabReceipt WHERE warehouse = %s
    """, (WAREHOUSE,))
    print(f"  Deleted Receipts")

    frappe.db.commit()
    print("\n=== Cleanup complete ===")
