from setuptools import setup, find_packages
import frappe

# Замість: from fin_core import __version__ as version
version = "0.0.1"


def create_system_analytics():
    system_accounts = [
        {"name": "SALES_REVENUE", "type": "Currency", "analytics_name": "Sales Revenue"},
        {"name": "COST_OF_GOODS_SOLD", "type": "Quantity", "analytics_name": "COGS"}
    ]

    for acc in system_accounts:
        if not frappe.db.exists("Analytics", acc["name"]):
            doc = frappe.get_doc({
                "doctype": "Analytics",
                "analytics_name": acc["name"],
                "type": acc["type"]
            })
            doc.insert(ignore_permissions=True)

setup(
    name="fin_core",
    version=version,
    description="Core Financial Engine",
    author="Solution Architect",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=[], # Можеш поки залишити порожнім
)