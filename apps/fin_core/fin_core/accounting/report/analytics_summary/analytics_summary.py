import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    if not filters: filters = {}

    columns = get_columns()
    data = get_data(filters)

    return columns, data


def get_columns():
    return [
        {
            "label": _("Аналітика"),
            "fieldname": "analytics",
            "fieldtype": "Link",
            "options": "Analytics", # Це критично для відображення як посилання
            "width": 160
        },
        {"label": _("Поч. залишок"), "fieldname": "opening_balance", "fieldtype": "Currency", "width": 120},
        {"label": _("Прихід (+)"), "fieldname": "inbound", "fieldtype": "Currency", "width": 120},
        {"label": _("Витрата (-)"), "fieldname": "outbound", "fieldtype": "Currency", "width": 120},
        {"label": _("Кінц. залишок"), "fieldname": "closing_balance", "fieldtype": "Currency", "width": 140},
    ]


def get_data(filters):
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")

    # Отримуємо всі аналітики
    analytics_list = frappe.get_all("Analytics", fields=["name"])
    report_data = []

    for a in analytics_list:
        name = a.name

        # 1. Рахуємо вхідний залишок (сума ТО - сума FROM до from_date)
        opening = frappe.db.sql("""
            SELECT 
                SUM(CASE WHEN to_analytics = %s THEN amount ELSE 0 END) -
                SUM(CASE WHEN from_analytics = %s THEN amount ELSE 0 END) as balance
            FROM `tabFin Transaction`
            WHERE (to_analytics = %s OR from_analytics = %s)
              AND docstatus = 1
              AND date < %s
        """, (name, name, name, name, from_date), as_dict=1)[0].balance or 0.0

        # 2. Рахуємо обороти за період
        period_data = frappe.db.sql("""
            SELECT 
                SUM(CASE WHEN to_analytics = %s THEN amount ELSE 0 END) as inbound,
                SUM(CASE WHEN from_analytics = %s THEN amount ELSE 0 END) as outbound
            FROM `tabFin Transaction`
            WHERE docstatus = 1
              AND date BETWEEN %s AND %s
        """, (name, name, from_date, to_date), as_dict=1)[0]

        inbound = period_data.inbound or 0.0
        outbound = period_data.outbound or 0.0
        closing = opening + inbound - outbound

        if opening != 0 or inbound != 0 or outbound != 0:
            report_data.append({
                "analytics": name,
                "opening_balance": opening,
                "inbound": inbound,
                "outbound": outbound,
                "closing_balance": closing
            })

    return report_data