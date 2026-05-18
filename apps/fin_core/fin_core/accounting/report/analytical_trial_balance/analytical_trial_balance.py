import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    _validate(filters)
    columns = _get_columns()
    data = _get_data(filters)
    return columns, data


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def _get_columns():
    return [
        {
            "label": _("Рахунок аналітики"),
            "fieldname": "analytics_account",
            "fieldtype": "Data",
            "width": 220,
        },
        {
            "label": _("Поч. дебет"),
            "fieldname": "opening_debit",
            "fieldtype": "Currency",
            "width": 130,
        },
        {
            "label": _("Поч. кредит"),
            "fieldname": "opening_credit",
            "fieldtype": "Currency",
            "width": 130,
        },
        {
            "label": _("Оборот дебет"),
            "fieldname": "period_debit",
            "fieldtype": "Currency",
            "width": 130,
        },
        {
            "label": _("Оборот кредит"),
            "fieldname": "period_credit",
            "fieldtype": "Currency",
            "width": 130,
        },
        {
            "label": _("Кінц. дебет"),
            "fieldname": "closing_debit",
            "fieldtype": "Currency",
            "width": 130,
        },
        {
            "label": _("Кінц. кредит"),
            "fieldname": "closing_credit",
            "fieldtype": "Currency",
            "width": 130,
        },
    ]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def _get_data(filters):
    conditions, params = _build_conditions(filters)

    opening_rows = _query_opening(filters, conditions, params)
    period_rows  = _query_period(filters, conditions, params)

    rows = _merge(opening_rows, period_rows)
    rows = _drop_empty(rows)
    rows.sort(key=lambda r: r["analytics_account"])

    if rows:
        rows.append(_total_row(rows))

    return rows


def _query_opening(filters, conditions, params):
    """Single GROUP BY query: net balance per account BEFORE from_date."""
    from_date = filters.get("from_date")
    if not from_date:
        return {}

    date_cond = "posting_date < %(from_date)s"
    where = f"{date_cond} AND {conditions}" if conditions else date_cond

    sql = f"""
        SELECT
            analytics_account,
            SUM(debit)  AS total_debit,
            SUM(credit) AS total_credit
        FROM `tabAnalytical Posting`
        WHERE {where}
        GROUP BY analytics_account
    """
    rows = frappe.db.sql(sql, {**params, "from_date": from_date}, as_dict=True)

    result = {}
    for r in rows:
        net = flt(r.total_debit) - flt(r.total_credit)
        result[r.analytics_account] = {
            "opening_debit":  max(net, 0.0),
            "opening_credit": max(-net, 0.0),
        }
    return result


def _query_period(filters, conditions, params):
    """Single GROUP BY query: gross debit/credit per account in the period."""
    from_date = filters.get("from_date")
    to_date   = filters.get("to_date")

    date_parts = []
    if from_date:
        date_parts.append("posting_date >= %(from_date)s")
    if to_date:
        date_parts.append("posting_date <= %(to_date)s")

    date_cond = " AND ".join(date_parts) if date_parts else "1=1"
    where = f"{date_cond} AND {conditions}" if conditions else date_cond

    sql = f"""
        SELECT
            analytics_account,
            SUM(debit)  AS period_debit,
            SUM(credit) AS period_credit
        FROM `tabAnalytical Posting`
        WHERE {where}
        GROUP BY analytics_account
    """
    rows = frappe.db.sql(sql, {**params, "from_date": from_date, "to_date": to_date}, as_dict=True)

    return {
        r.analytics_account: {
            "period_debit":  flt(r.period_debit),
            "period_credit": flt(r.period_credit),
        }
        for r in rows
    }


def _merge(opening: dict, period: dict) -> list[dict]:
    """Union of all accounts from both queries into a single row list."""
    all_accounts = set(opening) | set(period)
    rows = []
    for account in all_accounts:
        op  = opening.get(account, {"opening_debit": 0.0, "opening_credit": 0.0})
        per = period.get(account, {"period_debit":   0.0, "period_credit":  0.0})

        # Closing = opening_debit - opening_credit + period_debit - period_credit
        net_closing = (
            op["opening_debit"] - op["opening_credit"]
            + per["period_debit"] - per["period_credit"]
        )

        rows.append({
            "analytics_account": account,
            "opening_debit":     op["opening_debit"],
            "opening_credit":    op["opening_credit"],
            "period_debit":      per["period_debit"],
            "period_credit":     per["period_credit"],
            "closing_debit":     max(net_closing, 0.0),
            "closing_credit":    max(-net_closing, 0.0),
        })
    return rows


def _drop_empty(rows: list[dict]) -> list[dict]:
    """Remove rows where all six monetary columns are zero."""
    return [
        r for r in rows
        if any(
            r[col] != 0.0
            for col in (
                "opening_debit", "opening_credit",
                "period_debit",  "period_credit",
                "closing_debit", "closing_credit",
            )
        )
    ]


def _total_row(rows: list[dict]) -> dict:
    totals = {
        "analytics_account": _("Разом"),
        "opening_debit":     sum(r["opening_debit"]  for r in rows),
        "opening_credit":    sum(r["opening_credit"] for r in rows),
        "period_debit":      sum(r["period_debit"]   for r in rows),
        "period_credit":     sum(r["period_credit"]  for r in rows),
        "closing_debit":     sum(r["closing_debit"]  for r in rows),
        "closing_credit":    sum(r["closing_credit"] for r in rows),
        "_is_total": True,
    }
    return totals


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _validate(filters):
    if filters.get("from_date") and filters.get("to_date"):
        if filters["from_date"] > filters["to_date"]:
            frappe.throw(_("Дата 'З' не може бути пізніше дати 'По'"))


def _build_conditions(filters) -> tuple[str, dict]:
    """Build extra WHERE snippets and param dict for optional filters."""
    parts = []
    params = {}

    if filters.get("analytics_account"):
        parts.append("analytics_account = %(analytics_account)s")
        params["analytics_account"] = filters["analytics_account"]

    if filters.get("source_doctype"):
        parts.append("source_doctype = %(source_doctype)s")
        params["source_doctype"] = filters["source_doctype"]

    return (" AND ".join(parts), params)
