import frappe
from frappe.utils import today, flt


# ---------------------------------------------------------------------------
# Document CRUD
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_receipt(name: str) -> dict:
    doc = frappe.get_doc("Receipt", name)
    return _serialize(doc)


@frappe.whitelist()
def save_receipt(doc: dict) -> dict:
    import json
    if isinstance(doc, str):
        doc = json.loads(doc)

    name = doc.get("name")
    if name and frappe.db.exists("Receipt", name):
        receipt = frappe.get_doc("Receipt", name)
        receipt.update(doc)
        receipt.save(ignore_permissions=True)
    else:
        receipt = frappe.get_doc({"doctype": "Receipt", **doc})
        receipt.insert(ignore_permissions=True)

    frappe.db.commit()
    return _serialize(receipt)


@frappe.whitelist()
def submit_receipt(name: str) -> dict:
    receipt = frappe.get_doc("Receipt", name)
    if receipt.docstatus != 0:
        frappe.throw(f"Документ {name} не є чернеткою")
    receipt.submit()
    frappe.db.commit()
    return _serialize(receipt)


@frappe.whitelist()
def cancel_receipt(name: str) -> dict:
    receipt = frappe.get_doc("Receipt", name)
    if receipt.docstatus != 1:
        frappe.throw(f"Документ {name} не проведений")
    receipt.cancel()
    frappe.db.commit()
    return _serialize(receipt)


# ---------------------------------------------------------------------------
# Ledger preview
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_postings(source_name: str) -> list:
    rows = frappe.get_all(
        "Analytical Posting",
        filters={"source_doctype": "Receipt", "source_name": source_name},
        fields=["rule_name", "analytics_account", "debit", "credit", "qty", "stock_unit"],
        order_by="creation asc",
        limit=500,
    )
    return rows


@frappe.whitelist()
def get_template_preview(doc: dict) -> dict:
    """
    Returns what postings *would* look like given the current draft totals,
    grouped by account for the balance indicator.
    """
    import json
    if isinstance(doc, str):
        doc = json.loads(doc)

    templates = frappe.get_all(
        "Transaction Template",
        filters={"reference_doctype": "Receipt", "is_disabled": 0},
        fields=["name"],
    )
    if not templates:
        return {"balanced": True, "rows": [], "total_debit": 0, "total_credit": 0}

    total_debit = 0.0
    total_credit = 0.0
    rows = []

    items = doc.get("items") or []
    for tmpl_ref in templates:
        tmpl = frappe.get_doc("Transaction Template", tmpl_ref.name)
        for rule in tmpl.rules:
            for item in items:
                qty  = flt(item.get("qty", 0))
                rate = flt(item.get("rate", 0))
                amt  = qty * rate

                if rule.calculation_type == "Amount" and amt:
                    debit  = amt if rule.debit_credit == "Debit"  else 0
                    credit = amt if rule.debit_credit == "Credit" else 0
                    total_debit  += debit
                    total_credit += credit
                    rows.append({
                        "rule_name":        rule.rule_name,
                        "analytics_account": rule.target_value
                            if rule.target_type == "Constant"
                            else f"[{rule.target_value}]",
                        "debit":  debit,
                        "credit": credit,
                        "qty":    0,
                    })
                elif rule.calculation_type == "Quantity" and qty:
                    signed_qty = qty if rule.debit_credit == "Debit" else -qty
                    rows.append({
                        "rule_name":        rule.rule_name,
                        "analytics_account": rule.target_value,
                        "debit":  0,
                        "credit": 0,
                        "qty":    signed_qty,
                    })

    balanced = abs(total_debit - total_credit) < 0.01
    return {
        "balanced":     balanced,
        "rows":         rows,
        "total_debit":  round(total_debit, 2),
        "total_credit": round(total_credit, 2),
    }


# ---------------------------------------------------------------------------
# Options loaders
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_analytics_options(type_filter: str = "") -> list:
    filters = {}
    if type_filter:
        filters["type"] = type_filter
    return frappe.get_all(
        "Analytics",
        filters=filters,
        fields=["analytics_name as value", "analytics_name as label", "type"],
        order_by="analytics_name asc",
        limit=500,
    )


@frappe.whitelist()
def get_warehouse_options() -> list:
    return frappe.get_all(
        "Warehouse",
        fields=["name as value", "name as label"],
        order_by="name asc",
        limit=200,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _serialize(doc) -> dict:
    d = doc.as_dict()
    d["items"] = [
        {
            "name":           r.name,
            "item_analytics": r.item_analytics,
            "qty":            float(r.qty or 0),
            "rate":           float(r.rate or 0),
            "amount":         float(r.amount or 0),
        }
        for r in doc.items
    ]
    return d
