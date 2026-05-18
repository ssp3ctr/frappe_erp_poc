"""
Batch Realization — business logic.
Answers: "Where did all the inventory from this Receipt end up?"

Direction: Receipt root unit(s) → all leaf descendants (downward CTE).
Leaf = a Stock Unit whose status is terminal (not TRANSFERRED/TRANSFORMED,
which are intermediate split markers).

Leaf categories:
  Sold      → aggregated per Fin Order  (CONSUME_RESERVED path)
  Consumed  → aggregated per Fin Order  (SPEND path)
  Reserved  → aggregated per Sales Order (reservation_name)
  Available → aggregated per Warehouse   (still in stock)
  Adjusted  → aggregated globally       (cancelled / reversed units)

Consumed by: fin_core.accounting.report.batch_realization (Frappe Script Report).
"""

from collections import defaultdict

import frappe
from frappe import _

# ── public API ────────────────────────────────────────────────────────────────

def get_columns() -> list[dict]:
    return [
        {
            "label": _("Item"),
            "fieldname": "item_code",
            "fieldtype": "Link",
            "options": "Analytics",
            "width": 180,
        },
        {
            "label": _("Destination Document"),
            "fieldname": "destination",
            "fieldtype": "Data",
            "width": 260,
        },
        {
            "label": _("Customer / Purpose"),
            "fieldname": "customer",
            "fieldtype": "Link",
            "options": "Analytics",
            "width": 180,
        },
        {
            "label": _("Quantity"),
            "fieldname": "qty",
            "fieldtype": "Float",
            "width": 100,
        },
        {
            "label": _("Status"),
            "fieldname": "status",
            "fieldtype": "Data",
            "width": 110,
        },
        {
            "label": _("Date"),
            "fieldname": "date",
            "fieldtype": "Date",
            "width": 110,
        },
    ]


def get_data(filters: dict) -> list[dict]:
    receipt_id = filters.get("receipt_id")
    if not receipt_id:
        return []

    leaves = _fetch_leaf_nodes(receipt_id)
    if not leaves:
        return []

    rows = _aggregate(leaves)
    _enrich_metadata(rows)
    return _sort(rows)


# ── CTE query ─────────────────────────────────────────────────────────────────

def _fetch_leaf_nodes(receipt_id: str) -> list:
    """
    Downward CTE: start from Receipt-created units, collect every descendant
    that carries a terminal status (not an intermediate split node).
    """
    return frappe.db.sql(
        """
        WITH RECURSIVE tree AS (
            -- Roots: units whose origin is this Receipt
            SELECT
                su.name, su.parent_unit_id, su.item_code, su.warehouse,
                su.qty, su.status,
                su.source_doctype, su.source_name,
                su.reservation_name, su.posting_date
            FROM `tabStock Unit` su
            WHERE su.source_doctype = 'Receipt'
              AND su.source_name    = %(receipt_id)s

            UNION ALL

            -- Every descendant
            SELECT
                su.name, su.parent_unit_id, su.item_code, su.warehouse,
                su.qty, su.status,
                su.source_doctype, su.source_name,
                su.reservation_name, su.posting_date
            FROM `tabStock Unit` su
            INNER JOIN tree t ON su.parent_unit_id = t.name
        )
        SELECT
            name, item_code, warehouse,
            qty, status,
            source_doctype, source_name,
            reservation_name, posting_date
        FROM tree
        WHERE status IN ('Sold', 'Consumed', 'Available', 'Reserved', 'Adjusted')
        ORDER BY item_code, status, source_name NULLS LAST
        """,
        {"receipt_id": receipt_id},
        as_dict=True,
    )


# ── aggregation ───────────────────────────────────────────────────────────────

def _aggregate(leaves: list) -> list[dict]:
    """
    Collapse leaf rows into one dict per (item, destination_type, doc_name).
    Extra keys _destination_type and _destination_doc are forwarded to
    _enrich_metadata() and then surfaced to the JS formatter as hidden fields.
    """
    buckets: dict[tuple, dict] = {}

    for leaf in leaves:
        item      = leaf["item_code"]
        status    = leaf["status"]
        warehouse = leaf.get("warehouse") or ""

        if status in ("Sold", "Consumed"):
            doc_name = leaf.get("source_name") or ""
            key      = (item, "Fin Order", doc_name)
            label    = f"Fin Order: {doc_name}" if doc_name else "Fin Order (unknown)"
            dest_type = "Fin Order"

        elif status == "Reserved":
            doc_name = leaf.get("reservation_name") or ""
            key      = (item, "Sales Order", doc_name)
            label    = f"Sales Order: {doc_name}" if doc_name else "Sales Order (unknown)"
            dest_type = "Sales Order"

        elif status == "Available":
            doc_name = ""
            key      = (item, "Available", warehouse)
            label    = f"Warehouse Stock ({warehouse})" if warehouse else "Warehouse Stock"
            dest_type = "Available"

        else:  # Adjusted
            doc_name = ""
            key      = (item, "Adjusted", "")
            label    = "Cancelled / Adjusted"
            dest_type = "Adjusted"

        if key not in buckets:
            buckets[key] = {
                "item_code":          item,
                "destination":        label,
                "customer":           None,
                "qty":                0,
                "status":             status,
                "date":               None,
                "warehouse":          warehouse,
                # hidden — used for enrichment and JS formatter
                "_destination_type":  dest_type,
                "_destination_doc":   doc_name,
            }

        buckets[key]["qty"] += leaf["qty"]

    return list(buckets.values())


# ── metadata enrichment ───────────────────────────────────────────────────────

def _enrich_metadata(rows: list[dict]) -> None:
    """
    Batch-fetch customer_analytics and posting_date from Fin Orders / Sales
    Orders.  Mutates rows in-place; one DB round-trip per doctype.
    """
    fin_order_names = {
        r["_destination_doc"]
        for r in rows
        if r["_destination_type"] == "Fin Order" and r["_destination_doc"]
    }
    so_names = {
        r["_destination_doc"]
        for r in rows
        if r["_destination_type"] == "Sales Order" and r["_destination_doc"]
    }

    fin_orders: dict[str, dict] = {}
    if fin_order_names:
        for row in frappe.get_all(
            "Fin Order",
            filters={"name": ["in", list(fin_order_names)]},
            fields=["name", "customer_analytics", "posting_date"],
        ):
            fin_orders[row["name"]] = row

    sales_orders: dict[str, dict] = {}
    if so_names:
        for row in frappe.get_all(
            "Sales Order",
            filters={"name": ["in", list(so_names)]},
            fields=["name", "customer_analytics", "posting_date"],
        ):
            sales_orders[row["name"]] = row

    for row in rows:
        dest_type = row["_destination_type"]
        doc_name  = row["_destination_doc"]

        if dest_type == "Fin Order":
            meta = fin_orders.get(doc_name)
        elif dest_type == "Sales Order":
            meta = sales_orders.get(doc_name)
        else:
            meta = None

        if meta:
            row["customer"] = meta.get("customer_analytics")
            row["date"]     = meta.get("posting_date")


# ── sort ──────────────────────────────────────────────────────────────────────

_STATUS_ORDER = {"Sold": 0, "Consumed": 1, "Reserved": 2, "Available": 3, "Adjusted": 4}


def _sort(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (
            r["item_code"] or "",
            _STATUS_ORDER.get(r["status"], 9),
            r["_destination_doc"] or "",
        ),
    )
