import json

import frappe
from frappe.utils import today, flt

# Fields the frontend is allowed to set on the parent Receipt document.
# Keeping this explicit prevents any stale Frappe-internal fields (docstatus,
# amended_from, owner, …) that may leak from a previous as_dict() response
# from reaching insert()/save() and triggering unexpected validation errors.
_RECEIPT_FIELDS = frozenset({"naming_series", "posting_date", "warehouse", "customer_analytics"})

# Fields allowed on each Receipt Item row.
_ITEM_FIELDS = frozenset({"name", "item_analytics", "qty", "rate", "amount"})

_DEFAULT_SERIES = "RCP-.YYYY.-.#####"


# ---------------------------------------------------------------------------
# Document CRUD
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_receipt(name: str) -> dict:
    doc = frappe.get_doc("Receipt", name)
    return _serialize(doc)


@frappe.whitelist()
def save_receipt(doc: str) -> dict:
    """Create or update a Receipt draft. Returns the serialised document."""
    if isinstance(doc, str):
        doc = json.loads(doc)

    payload = _extract_payload(doc)
    name = doc.get("name")

    if name and frappe.db.exists("Receipt", name):
        receipt = frappe.get_doc("Receipt", name)
        receipt.update(payload)
        receipt.save(ignore_permissions=True)
    else:
        payload.setdefault("naming_series", _DEFAULT_SERIES)
        receipt = frappe.get_doc({"doctype": "Receipt", **payload})
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

def _extract_payload(doc: dict) -> dict:
    """Return only the fields the frontend is allowed to set."""
    payload = {k: doc[k] for k in _RECEIPT_FIELDS if k in doc}
    payload["items"] = [
        {k: row[k] for k in _ITEM_FIELDS if k in row}
        for row in (doc.get("items") or [])
    ]
    return payload


def _serialize(doc) -> dict:
    """Return the minimal dict the frontend needs, with no Frappe internals."""
    return {
        "name":               doc.name,
        "doctype":            "Receipt",
        "naming_series":      doc.naming_series,
        "docstatus":          doc.docstatus,
        "posting_date":       str(doc.posting_date) if doc.posting_date else None,
        "warehouse":          doc.warehouse,
        "customer_analytics": doc.customer_analytics,
        "total_amount":       float(doc.total_amount or 0),
        "items": [
            {
                "name":           r.name,
                "item_analytics": r.item_analytics,
                "qty":            float(r.qty or 0),
                "rate":           float(r.rate or 0),
                "amount":         float(r.amount or 0),
            }
            for r in doc.items
        ],
    }
