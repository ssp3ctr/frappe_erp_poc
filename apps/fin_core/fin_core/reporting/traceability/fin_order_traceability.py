"""
Fin Order Traceability — business logic.
Answers: "Which Receipt batches supplied the items for this Fin Order?"

Direction: Fin Order consumed/sold units → ancestors → originating Receipt (upward CTE).
Consumed by: fin_core.accounting.report.fin_order_traceability (Frappe Script Report).

Two terminal conditions per CTE thread:
  - source_doctype = 'Receipt'            → fully traced; join Receipt for metadata.
  - depth = 0 AND parent_unit_id IS NULL  → whole-unit consumption; origin was
                                            overwritten when source was re-stamped
                                            to the Fin Order; surfaced as untraced
                                            so no row is silently dropped.
"""

from collections import defaultdict

import frappe
from frappe import _


def get_columns() -> list[dict]:
    return [
        {
            "label": _("Item"),
            "fieldname": "item_code",
            "fieldtype": "Link",
            "options": "Analytics",
            "width": 200,
        },
        {
            "label": _("Qty"),
            "fieldname": "qty",
            "fieldtype": "Float",
            "width": 90,
        },
        {
            "label": _("Source Receipt"),
            "fieldname": "source_batch",
            "fieldtype": "Link",
            "options": "Receipt",
            "width": 180,
        },
        {
            "label": _("Receipt Date"),
            "fieldname": "source_date",
            "fieldtype": "Date",
            "width": 120,
        },
        {
            "label": _("Rate"),
            "fieldname": "rate",
            "fieldtype": "Currency",
            "options": "UAH",
            "width": 110,
        },
        {
            "label": _("Warehouse"),
            "fieldname": "warehouse",
            "fieldtype": "Link",
            "options": "Warehouse",
            "width": 180,
        },
        {
            "label": _("Stock Unit"),
            "fieldname": "stock_unit",
            "fieldtype": "Link",
            "options": "Stock Unit",
            "width": 200,
        },
    ]


def get_data(filters: dict) -> list[dict]:
    fin_order = filters.get("source_name")
    if not fin_order:
        return []
    flat = _fetch_traced_sources(fin_order)
    return _build_grouped_rows(flat)


def _fetch_traced_sources(fin_order: str) -> list:
    return frappe.db.sql(
        """
        WITH RECURSIVE upward AS (

            -- Anchor: units consumed / sold by this Fin Order
            SELECT
                su.name             AS leaf_name,
                su.item_code        AS leaf_item_code,
                su.qty              AS leaf_qty,
                su.warehouse        AS leaf_warehouse,
                su.parent_unit_id   AS parent_unit_id,
                su.source_doctype   AS source_doctype,
                su.source_name      AS source_name,
                0                   AS depth
            FROM `tabStock Unit` su
            WHERE su.source_doctype = 'Fin Order'
              AND su.source_name    = %(fin_order)s
              AND su.status         IN ('Sold', 'Consumed')

            UNION ALL

            -- Walk up the parent chain
            SELECT
                child.leaf_name,
                child.leaf_item_code,
                child.leaf_qty,
                child.leaf_warehouse,
                parent.parent_unit_id,
                parent.source_doctype,
                parent.source_name,
                child.depth + 1
            FROM `tabStock Unit` parent
            INNER JOIN upward child ON parent.name = child.parent_unit_id
            WHERE child.source_doctype != 'Receipt'
              AND child.depth < 50
        )

        SELECT
            u.leaf_item_code    AS item_code,
            u.leaf_qty          AS qty,
            CASE WHEN u.source_doctype = 'Receipt'
                 THEN u.source_name ELSE NULL END    AS source_batch,
            r.posting_date                           AS source_date,
            ri.rate                                  AS rate,
            u.leaf_warehouse                         AS warehouse,
            u.leaf_name                              AS stock_unit,
            CASE WHEN u.source_doctype != 'Receipt'
                 THEN 1 ELSE 0 END                   AS _untraced
        FROM upward u
        LEFT JOIN `tabReceipt` r
               ON r.name = u.source_name
              AND u.source_doctype = 'Receipt'
        LEFT JOIN `tabReceipt Item` ri
               ON ri.parent        = u.source_name
              AND ri.item_analytics = u.leaf_item_code
              AND u.source_doctype  = 'Receipt'
        WHERE u.source_doctype = 'Receipt'
           OR (u.depth = 0 AND u.parent_unit_id IS NULL)
        ORDER BY u.leaf_item_code, u.source_name NULLS LAST
        """,
        {"fin_order": fin_order},
        as_dict=True,
    )


def _build_grouped_rows(flat: list) -> list[dict]:
    """
    Insert a bold group-header row per item (shows total qty) followed by
    indented sub-rows for each contributing Receipt batch.
    """
    groups: dict[str, list] = defaultdict(list)
    for row in flat:
        groups[row["item_code"]].append(row)

    result: list[dict] = []
    for item_code, rows in groups.items():
        total_qty = sum(r["qty"] for r in rows)

        result.append({
            "item_code": item_code,
            "qty": total_qty,
            "source_batch": None,
            "source_date": None,
            "rate": None,
            "warehouse": rows[0]["warehouse"],
            "stock_unit": None,
            "bold": 1,
            "_untraced": 0,
        })

        for row in rows:
            result.append({
                "item_code": None,
                "qty": row["qty"],
                "source_batch": row.get("source_batch"),
                "source_date": row.get("source_date"),
                "rate": row.get("rate"),
                "warehouse": row["warehouse"],
                "stock_unit": row.get("stock_unit"),
                "indent": 1,
                "_untraced": int(row.get("_untraced") or 0),
            })

    return result
