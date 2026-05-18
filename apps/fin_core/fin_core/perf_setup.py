"""
One-time setup for stress test catalog.
Creates: warehouse, 10 item Analytics, 5 customer AR, AP account, templates.
Safe to re-run (idempotent).
"""
import frappe
from frappe.utils import today

WAREHOUSE  = "STRESS_WAREHOUSE"
ITEMS      = [f"STRESS_ITEM_{i:02d}" for i in range(1, 11)]
CUSTOMERS  = [f"STRESS_CUSTOMER_{i:02d}_AR" for i in range(1, 6)]

RECEIPT_TMPL   = "RECEIPT_POSTINGS"
FIN_ORDER_TMPL = "FIN_ORDER_POSTINGS"

RECEIPT_RULES = [
    ("Inventory In",   "Amount",   "amount", "Constant", "INVENTORY",        "Debit",  1),
    ("AP Credit",      "Amount",   "amount", "Constant", "ACCOUNTS_PAYABLE", "Credit", 1),
    ("Inventory Qty+", "Quantity", "qty",    "Constant", "INVENTORY_QTY",    "Debit",  1),
]

FIN_ORDER_RULES = [
    ("COGS Amount",    "Amount",   "amount",       "Constant", "COST_OF_GOODS_SOLD", "Debit",  1),
    ("Inventory Out",  "Amount",   "amount",       "Constant", "INVENTORY",          "Credit", 1),
    ("Revenue",        "Amount",   "total_amount", "Constant", "SALES_REVENUE",      "Credit", 0),
    ("AR Debit",       "Amount",   "total_amount", "Field",    "customer_analytics", "Debit",  0),
    ("Inventory Qty-", "Quantity", "qty",          "Constant", "INVENTORY_QTY",      "Credit", 1),
]


def _ensure_analytics(name, atype):
    if not frappe.db.exists("Analytics", name):
        frappe.get_doc({"doctype": "Analytics", "analytics_name": name, "type": atype}).insert(ignore_permissions=True)
        return "Created"
    return "Exists"


def _ensure_template(name, opcode, ref_doctype, rules):
    if frappe.db.exists("Transaction Template", name):
        frappe.delete_doc("Transaction Template", name, ignore_permissions=True, force=True)
        frappe.db.commit()
    doc = frappe.get_doc({
        "doctype":           "Transaction Template",
        "template_name":     name,
        "operation_code":    opcode,
        "reference_doctype": ref_doctype,
        "is_disabled":       0,
        "rules": [
            {"rule_name": r[0], "calculation_type": r[1], "source_field": r[2],
             "target_type": r[3], "target_value": r[4], "debit_credit": r[5], "is_per_row": r[6]}
            for r in rules
        ],
    })
    doc.insert(ignore_permissions=True)


def run():
    print("\n=== Stress Test Setup ===")

    # Warehouse
    if not frappe.db.exists("Warehouse", WAREHOUSE):
        frappe.get_doc({"doctype": "Warehouse", "warehouse_name": WAREHOUSE}).insert(ignore_permissions=True)
        print(f"  Created Warehouse: {WAREHOUSE}")
    else:
        print(f"  Exists  Warehouse: {WAREHOUSE}")

    # Base analytics (ensure exist — may already be present from full-cycle test)
    for name in ("INVENTORY", "ACCOUNTS_PAYABLE", "INVENTORY_QTY",
                 "COST_OF_GOODS_SOLD", "SALES_REVENUE"):
        status = _ensure_analytics(name, "Currency" if name != "INVENTORY_QTY" else "Quantity")
        print(f"  {status:7s} Analytics: {name}")

    # AP placeholder for receipts
    status = _ensure_analytics("STRESS_AP", "Currency")
    print(f"  {status:7s} Analytics: STRESS_AP")

    # Items
    for item in ITEMS:
        status = _ensure_analytics(item, "Quantity")
        print(f"  {status:7s} Item Analytics: {item}")

    # Customers
    for cust in CUSTOMERS:
        status = _ensure_analytics(cust, "Currency")
        print(f"  {status:7s} Customer Analytics: {cust}")

    frappe.db.commit()

    # Templates
    _ensure_template(RECEIPT_TMPL,   "RECEIPT",   "Receipt",    RECEIPT_RULES)
    print(f"  (Re)created Template: {RECEIPT_TMPL}")
    _ensure_template(FIN_ORDER_TMPL, "FIN_ORDER", "Fin Order",  FIN_ORDER_RULES)
    print(f"  (Re)created Template: {FIN_ORDER_TMPL}")
    frappe.db.commit()

    print("\n=== Setup complete ===")
