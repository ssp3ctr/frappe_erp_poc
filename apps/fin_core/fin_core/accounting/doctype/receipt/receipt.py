import frappe
from frappe.model.document import Document
from frappe.utils import flt

from fin_core.models.stock_unit import StockUnitStatus
from fin_core.repositories.stock_unit_repository import StockUnitRepository


class Receipt(Document):

    def validate(self):
        for item in self.items:
            item.amount = flt(item.qty) * flt(item.rate)
        self.total_amount = sum(flt(d.amount) for d in self.items)

    def on_submit(self):
        frappe.enqueue(
            "fin_core.engine.utxo_engine.run_receipt_process",
            doc_name=self.name,
            enqueue_after_commit=True,
            queue="default",
        )

    def on_cancel(self):
        repo = StockUnitRepository()
        units = repo.find_by_source(self.doctype, self.name)
        for unit in units:
            unit.deactivate(StockUnitStatus.ADJUSTED)
        if units:
            repo.bulk_save(units)
