import frappe


def execute():
    units = frappe.db.sql("""
        SELECT name, source_doctype, source_name, creation
        FROM `tabStock Unit`
        WHERE posting_date IS NULL
    """, as_dict=True)

    print(f"Backfilling posting_date for {len(units)} Stock Units...")

    for unit in units:
        posting_date = _resolve_posting_date(unit)
        frappe.db.set_value(
            "Stock Unit", unit.name, "posting_date", posting_date, update_modified=False
        )

    print("Done.")


def _resolve_posting_date(unit):
    if unit.source_doctype and unit.source_name:
        try:
            pd = frappe.db.get_value(unit.source_doctype, unit.source_name, "posting_date")
            if pd:
                return pd
        except Exception:
            pass
    # Fall back to the date the record was physically created
    return unit.creation.date() if unit.creation else None
