"""
Stock Unit Lineage — business logic.
Answers: "Show me the full UTXO tree rooted at this batch."

Direction: root → all descendants (downward CTE).
Consumed by: fin_core.accounting.page.stock_unit_lineage.stock_unit_lineage (Frappe Page API).
"""

import frappe


def get_lineage(stock_unit: str) -> dict:
    root = _walk_to_root(stock_unit)
    flat = _fetch_descendants(root)
    return _build_tree(flat, root)


def _walk_to_root(name: str) -> str:
    """Climb parent_unit_id links until we reach a unit with no parent."""
    visited: set[str] = set()
    current = name
    while True:
        if current in visited:
            break
        visited.add(current)
        parent = frappe.db.get_value("Stock Unit", current, "parent_unit_id")
        if not parent:
            break
        current = parent
    return current


def _fetch_descendants(root: str) -> list[dict]:
    """Return the root and every descendant via a downward recursive CTE."""
    return frappe.db.sql(
        """
        WITH RECURSIVE tree AS (
            SELECT
                name, parent_unit_id, item_code, warehouse,
                qty, status, is_active,
                source_doctype, source_name,
                posting_date, creation
            FROM `tabStock Unit`
            WHERE name = %(root)s

            UNION ALL

            SELECT
                su.name, su.parent_unit_id, su.item_code, su.warehouse,
                su.qty, su.status, su.is_active,
                su.source_doctype, su.source_name,
                su.posting_date, su.creation
            FROM `tabStock Unit` su
            INNER JOIN tree t ON su.parent_unit_id = t.name
        )
        SELECT * FROM tree
        ORDER BY creation
        """,
        {"root": root},
        as_dict=True,
    )


def _build_tree(flat: list[dict], root_name: str) -> dict:
    """Convert a flat list into a nested dict tree."""
    index = {r["name"]: dict(r, children=[]) for r in flat}

    tree_root = None
    for node in index.values():
        parent_id = node.get("parent_unit_id")
        if parent_id and parent_id in index:
            index[parent_id]["children"].append(node)
        else:
            tree_root = node

    return tree_root or index.get(root_name) or {}
