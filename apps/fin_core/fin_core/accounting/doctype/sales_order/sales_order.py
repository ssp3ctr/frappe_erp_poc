import frappe
from frappe.model.document import Document
from frappe.utils import flt
from decimal import Decimal

from fin_core.models.transaction_request import TransactionRequest, TransactionType
from fin_core.engine.utxo_engine import get_engine


class SalesOrder(Document):
    def validate(self):
        self._calculate_totals()

    def on_submit(self):
        engine = get_engine()
        for item in self.items:
            engine.process(TransactionRequest(
                transaction_type=TransactionType.RESERVE,
                item_code=item.item_analytics,
                warehouse=self.warehouse,
                qty=Decimal(str(item.qty)),
                source_doctype=self.doctype,
                source_name=self.name,
                posting_date=self.posting_date,
            ))
        frappe.db.set_value("Sales Order", self.name, "status", "Reserved")

    def on_cancel(self):
        engine = get_engine()
        for item in self.items:
            engine.process(TransactionRequest(
                transaction_type=TransactionType.CANCEL_RESERVATION,
                item_code=item.item_analytics,
                warehouse=self.warehouse,
                qty=Decimal(str(item.qty)),
                source_doctype=self.doctype,
                source_name=self.name,
            ))
        frappe.db.set_value("Sales Order", self.name, "status", "Cancelled")

    def _calculate_totals(self):
        total = 0
        for item in self.items:
            item.amount = flt(item.qty) * flt(item.rate)
            total += item.amount
        self.total_amount = total
