import frappe
from frappe.model.document import Document


class LedgerEntry(Document):
    #def before_insert(self):
        # Забороняємо ручне створення записів поза двигуном
    #    if not frappe.flags.in_fin_engine:
    #        frappe.throw("Ledger Entry можна створювати тільки через фінансовий двигун.")

    @staticmethod
    def make_entries(fin_tx):
        """
        Створює пару записів (Double Entry) на основі Fin Transaction.
        """
        frappe.flags.in_fin_engine = True

        common_data = {
            "doctype": "Ledger Entry",
            "posting_date": fin_tx.posting_date,
            "finance_transaction": fin_tx.name,
            "reference_doctype": fin_tx.reference_doctype,
            "reference_name": fin_tx.reference_name,
        }

        # 1. Запис списання (Outflow)
        outflow = frappe.get_doc(dict(common_data, **{
            "analytics": fin_tx.from_analytics,
            "amount": -abs(fin_tx.amount or 0),
            "qty": -abs(fin_tx.qty or 0),
            "transaction_type": "Outflow"
        }))
        outflow.insert(ignore_permissions=True)

        # 2. Запис отримання (Inflow)
        inflow = frappe.get_doc(dict(common_data, **{
            "analytics": fin_tx.to_analytics,
            "amount": abs(fin_tx.amount or 0),
            "qty": abs(fin_tx.qty or 0),
            "transaction_type": "Inflow"
        }))
        inflow.insert(ignore_permissions=True)

        frappe.flags.in_fin_engine = False