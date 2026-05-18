import frappe
from fin_core.reporting.lineage.stock_unit_lineage import get_lineage as _get_lineage


@frappe.whitelist()
def get_lineage(stock_unit: str) -> dict:
    return _get_lineage(stock_unit)
