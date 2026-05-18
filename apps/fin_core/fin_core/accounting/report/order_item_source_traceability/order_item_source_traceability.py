"""
Order Item Source Traceability
==============================
Answers: "Which Receipt batches supplied the items for this Fin Order?"

For each Sold/Consumed Stock Unit stamped with the given Fin Order the CTE
walks the parent_unit_id chain upward until it reaches a unit whose
source_doctype = 'Receipt'.  Whole-unit consumptions (no parent chain, origin
lost when source was re-stamped) are surfaced as untraced rows so they are
never silently dropped.
"""

from collections import defaultdict

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


# ── columns ───────────────────────────────────────────────────────────────────

def get_columns():
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


# ── data ──────────────────────────────────────────────────────────────────────

def get_data(filters):
    fin_order = filters.get("source_name")
    if not fin_order:
        return []

    flat = _fetch_traced_sources(fin_order)
    return _build_grouped_rows(flat)


def _fetch_traced_sources(fin_order: str) -> list:
    """
    CTE climbs the parent_unit_id chain from every Sold/Consumed unit back to
    the originating Receipt unit.

    Terminal condition per thread:
      - source_doctype = 'Receipt'           → traced, join Receipt for metadata
      - depth = 0 AND parent_unit_id IS NULL → whole-unit consumption, untraced
    """
    return frappe.db.sql(
        """
        WITH RECURSIVE upward AS (

            -- Anchor: units directly consumed / sold by this Fin Order
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

            -- Recursive step: walk to parent
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
            -- Stop ascending once we reach a Receipt node; cap depth as safety
            WHERE child.source_doctype != 'Receipt'
              AND child.depth < 50
        )

        SELECT
            u.leaf_item_code    AS item_code,
            u.leaf_qty          AS qty,
            CASE
                WHEN u.source_doctype = 'Receipt' THEN u.source_name
                ELSE NULL
            END                 AS source_batch,
            r.posting_date      AS source_date,
            ri.rate             AS rate,
            u.leaf_warehouse    AS warehouse,
            u.leaf_name         AS stock_unit,
            -- expose for JS formatter
            CASE WHEN u.source_doctype != 'Receipt' THEN 1 ELSE 0 END AS _untraced
        FROM upward u
        LEFT JOIN `tabReceipt` r
               ON r.name = u.source_name
              AND u.source_doctype = 'Receipt'
        LEFT JOIN `tabReceipt Item` ri
               ON ri.parent       = u.source_name
              AND ri.item_analytics = u.leaf_item_code
              AND u.source_doctype = 'Receipt'

        -- Keep traced terminal rows (Receipt found) and untraced whole-unit rows
        WHERE u.source_doctype = 'Receipt'
           OR (u.depth = 0 AND u.parent_unit_id IS NULL)

        ORDER BY u.leaf_item_code, u.source_name NULLS LAST
        """,
        {"fin_order": fin_order},
        as_dict=True,
    )


def _build_grouped_rows(flat: list) -> list:
    """
    Converts flat CTE result into Frappe report rows with bold group headers
    (one per item_code) and indented sub-rows (one per source batch).
    """
    groups: dict[str, list] = defaultdict(list)
    for row in flat:
        groups[row["item_code"]].append(row)

    result = []
    for item_code, rows in groups.items():
        total_qty = sum(r["qty"] for r in rows)

        # Bold summary header — shows item + total qty consumed
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
                "item_code": None,          # blank — item already shown in header
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
