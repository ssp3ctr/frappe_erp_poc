import frappe


def execute():
    frappe.db.sql("DELETE FROM `tabFin Order Stock Unit`")

    submitted_orders = frappe.get_all("Fin Order", filters={"docstatus": 1}, pluck="name")
    print(f"Repopulating {len(submitted_orders)} submitted Fin Orders...")

    for doc_name in submitted_orders:
        frappe.get_doc("Fin Order", doc_name).refresh_stock_transactions()
        print(f"  Done: {doc_name}")

    print("Cleanup complete.")
