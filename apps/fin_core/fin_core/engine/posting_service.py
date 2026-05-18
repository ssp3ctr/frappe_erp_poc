"""
Async analytical posting service.

Invoked by the UTXO engine via frappe.enqueue after bulk_save commits.
Creates Analytical Posting records for each Stock Unit according to the
Transaction Template rules.

calculation_type == "Amount"   → fills debit / credit; qty = 0
calculation_type == "Quantity" → fills qty; debit = credit = 0
"""
import frappe
from frappe.utils import flt

_CALC_AMOUNT   = "Amount"
_CALC_QUANTITY = "Quantity"

_UNIT_FIELDS = [
    "name", "item_code", "warehouse", "qty", "amount",
    "characteristic", "quality", "supplier", "batch_no",
    "source_doctype", "source_name", "posting_date",
]


def create_postings_from_units(
    unit_names: list[str],
    source_doctype: str,
    source_name: str,
    template_name: str | None = None,
    posting_date: str | None = None,
) -> None:
    if not unit_names:
        return

    templates = _resolve_templates(template_name, source_doctype)
    if not templates:
        return

    # Serialize concurrent callers for the same source document under REPEATABLE
    # READ.  Without this lock, two background workers processing separate items
    # of the same multi-item document can both pass the idempotency check (each
    # reads "nothing posted yet" from its own snapshot) and produce duplicates.
    lock_key = abs(hash(source_name)) % (2 ** 31)
    frappe.db.sql("SELECT pg_advisory_xact_lock(%(k)s)", {"k": lock_key})

    units = frappe.get_all(
        "Stock Unit",
        filters={"name": ["in", unit_names]},
        fields=_UNIT_FIELDS,
    )
    if not units:
        return

    source_doc = frappe.get_doc(source_doctype, source_name)
    to_insert = []

    for tmpl in templates:
        template_doc = frappe.get_doc("Transaction Template", tmpl.name)
        if template_doc.is_disabled:
            continue

        # --- Per-row idempotency: skip units that already have a posting -----
        # Multi-item documents enqueue one job per item with the same source_name.
        # The second job must not skip all work just because the first job posted
        # its own units — only skip units that are already posted.
        already_posted: set[str] = {
            row.stock_unit
            for row in frappe.get_all(
                "Analytical Posting",
                filters={"source_name": source_name, "template_name": tmpl.name,
                         "stock_unit": ["not in", ["", None]]},
                fields=["stock_unit"],
            )
        }
        new_units = [u for u in units if u.name not in already_posted]

        # --- Aggregate idempotency: run once per source document --------------
        # Aggregate postings (is_per_row=0) have stock_unit=None; they must be
        # created exactly once regardless of how many item batches are processed.
        aggregate_exists = bool(frappe.db.sql("""
            SELECT 1 FROM "tabAnalytical Posting"
            WHERE source_name = %(sn)s AND template_name = %(tn)s
              AND (stock_unit IS NULL OR stock_unit = '')
            LIMIT 1
        """, {"sn": source_name, "tn": tmpl.name}))

        has_aggregate_rules = any(not r.is_per_row for r in template_doc.rules)
        if not new_units and (aggregate_exists or not has_aggregate_rules):
            frappe.logger().debug(
                f"posting_service: all postings already exist for "
                f"{source_name} (template={tmpl.name}) — skipping"
            )
            continue

        for rule in template_doc.rules:
            if rule.is_per_row:
                for unit in new_units:
                    entry = _build_entry(rule, unit, source_doc, template_doc.name, posting_date)
                    if entry:
                        to_insert.append(entry)
            else:
                if not aggregate_exists:
                    # Pass full original unit list so aggregate value is correct
                    entry = _build_aggregate_entry(
                        rule, units, source_doc, template_doc.name, posting_date
                    )
                    if entry:
                        to_insert.append(entry)

    for entry in to_insert:
        frappe.get_doc(entry).insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Per-unit entry builder
# ---------------------------------------------------------------------------

def _build_entry(rule, unit, source_doc, template_name: str, posting_date: str | None) -> dict | None:
    account = _resolve_account(rule, unit, source_doc)
    if not account:
        return None

    calc_type = rule.get("calculation_type") or _CALC_AMOUNT
    value = _resolve_value(rule, unit, source_doc, calc_type)
    if value == 0:
        return None

    return _make_doc(
        calc_type=calc_type,
        value=value,
        debit_credit=rule.debit_credit,
        account=account,
        template_name=template_name,
        rule_name=rule.rule_name,
        posting_date=posting_date or unit.get("posting_date") or frappe.utils.today(),
        source_doctype=unit.get("source_doctype") or source_doc.doctype,
        source_name=unit.get("source_name") or source_doc.name,
        stock_unit=unit.name,
    )


# ---------------------------------------------------------------------------
# Aggregate entry builder (is_per_row = False)
# ---------------------------------------------------------------------------

def _build_aggregate_entry(rule, units: list, source_doc, template_name: str, posting_date: str | None) -> dict | None:
    account = _resolve_account(rule, None, source_doc)
    if not account:
        return None

    calc_type = rule.get("calculation_type") or _CALC_AMOUNT
    total = _resolve_aggregate_value(rule, units, source_doc, calc_type)
    if total == 0:
        return None

    return _make_doc(
        calc_type=calc_type,
        value=total,
        debit_credit=rule.debit_credit,
        account=account,
        template_name=template_name,
        rule_name=rule.rule_name,
        posting_date=posting_date or source_doc.get("posting_date") or frappe.utils.today(),
        source_doctype=source_doc.doctype,
        source_name=source_doc.name,
        stock_unit=None,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_doc(
    *,
    calc_type: str,
    value: float,
    debit_credit: str,
    account: str,
    template_name: str,
    rule_name: str,
    posting_date: str,
    source_doctype: str,
    source_name: str,
    stock_unit: str | None,
) -> dict:
    if calc_type == _CALC_QUANTITY:
        # Signed quantity: positive = Debit (inflow), negative = Credit (outflow)
        # Enables SUM(qty) to yield net balance directly in trial balance queries.
        debit, credit = 0.0, 0.0
        qty = value if debit_credit == "Debit" else -value
    else:
        debit  = value if debit_credit == "Debit"  else 0.0
        credit = value if debit_credit == "Credit" else 0.0
        qty    = 0.0

    return {
        "doctype":        "Analytical Posting",
        "source_doctype": source_doctype,
        "source_name":    source_name,
        "template_name":  template_name,
        "rule_name":      rule_name,
        "posting_date":   posting_date,
        "stock_unit":     stock_unit,
        "analytics_account": account,
        "debit":  debit,
        "credit": credit,
        "qty":    qty,
    }


def _resolve_account(rule, unit, source_doc) -> str | None:
    if rule.target_type == "Constant":
        return rule.target_value or None
    # "Field" — look on unit first, then source document
    value = unit.get(rule.target_value) if unit else None
    if not value and source_doc:
        value = source_doc.get(rule.target_value)
    return value or None


def _resolve_field_name(rule, calc_type: str) -> str:
    """Return the field name to use for numeric value resolution.

    Priority:
      1. rule.source_field          (primary — explicit configuration)
      2. rule.source_amount_field   (legacy Amount)
      3. rule.source_qty_field      (legacy Quantity)
      4. built-in default by calc_type
    """
    return (
        rule.get("source_field")
        or (rule.get("source_amount_field") if calc_type == _CALC_AMOUNT else None)
        or (rule.get("source_qty_field")    if calc_type == _CALC_QUANTITY else None)
        or ("amount" if calc_type == _CALC_AMOUNT else "qty")
    )


def _resolve_value(rule, unit, source_doc, calc_type: str) -> float:
    """Read the numeric value for a single unit (per-row rules)."""
    field = _resolve_field_name(rule, calc_type)
    val = unit.get(field) if unit else None
    if val is None and source_doc:
        val = source_doc.get(field)
    return flt(val)


def _resolve_aggregate_value(rule, units: list, source_doc, calc_type: str) -> float:
    """Aggregate value for is_per_row=False rules.

    If the source_field exists on units (unit-level field like "amount" or "qty"),
    sum it across all units.
    If the field is absent from every unit (doc-level field like "total_amount"),
    read it exactly once from the source document — NOT multiplied by unit count.
    """
    field = _resolve_field_name(rule, calc_type)
    unit_has_field = any(u.get(field) is not None for u in units)
    if unit_has_field:
        return sum(flt(u.get(field)) for u in units)
    return flt(source_doc.get(field))


def _resolve_templates(template_name: str | None, source_doctype: str) -> list:
    if template_name:
        return [frappe._dict(name=template_name)]
    return frappe.get_all(
        "Transaction Template",
        filters={"reference_doctype": source_doctype, "is_disabled": 0},
        fields=["name"],
    )
