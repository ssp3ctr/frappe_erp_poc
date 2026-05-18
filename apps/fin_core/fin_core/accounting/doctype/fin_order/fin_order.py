import frappe
from frappe.model.document import Document
from frappe.utils import flt
from decimal import Decimal

from fin_core.models.stock_unit import StockUnitStatus
from fin_core.models.transaction_request import TransactionRequest, TransactionType
from fin_core.engine.utxo_engine import get_engine
from fin_core.repositories.stock_unit_repository import StockUnitRepository


class FinOrder(Document):
    def validate(self):
        self.calculate_totals()

    def on_submit(self):
        engine = get_engine()

        if self.sales_order:
            # Fulfil an existing reservation — consume all reserved units for that SO.
            for item in self.items:
                engine.process(TransactionRequest(
                    transaction_type=TransactionType.CONSUME_RESERVED,
                    item_code=item.item_analytics,
                    warehouse=self.warehouse,
                    qty=Decimal(str(item.qty)),
                    source_doctype=self.doctype,
                    source_name=self.name,
                    reservation_name=self.sales_order,
                ))
            frappe.db.set_value("Sales Order", self.sales_order, "status", "Fulfilled")
        else:
            # Direct sale — consume from available stock (no prior reservation).
            for item in self.items:
                engine.process(TransactionRequest(
                    transaction_type=TransactionType.SPEND,
                    item_code=item.item_analytics,
                    warehouse=self.warehouse,
                    qty=Decimal(str(item.qty)),
                    source_doctype=self.doctype,
                    source_name=self.name,
                ))

        frappe.enqueue(
            "fin_core.accounting.doctype.fin_order.fin_order.run_refresh_stock_transactions",
            doc_name=self.name,
            enqueue_after_commit=True,
            queue="default",
        )

    def on_cancel(self):
        repo = StockUnitRepository()
        children = repo.find_by_source(self.doctype, self.name)
        to_save = []
        for child in children:
            if child.parent_unit_id:
                parent = repo.get(child.parent_unit_id)
                parent.is_active = True
                parent.status = StockUnitStatus.AVAILABLE
                to_save.append(parent)
            child.is_active = False
            child.status = StockUnitStatus.ADJUSTED
            to_save.append(child)
        if to_save:
            repo.bulk_save(to_save)

        # Re-open the linked Sales Order if this Fin Order fulfilled it.
        if self.sales_order:
            frappe.db.set_value("Sales Order", self.sales_order, "status", "Reserved")

    def refresh_stock_transactions(self):
        repo = StockUnitRepository()
        all_units = repo.find_by_source(self.doctype, self.name)
        spent_units = [u for u in all_units if u.status in (StockUnitStatus.CONSUMED, StockUnitStatus.SOLD)]
        frappe.db.delete("Fin Order Stock Unit", {"parent": self.name})
        for idx, unit in enumerate(spent_units, start=1):
            child = frappe.get_doc({
                "doctype": "Fin Order Stock Unit",
                "parent": self.name,
                "parenttype": self.doctype,
                "parentfield": "stock_units",
                "idx": idx,
                "stock_unit": unit.name,
                "item": unit.item_code,
                "warehouse": unit.warehouse,
                "qty": float(unit.qty),
                "status": unit.status,
                "parent_unit_id": unit.parent_unit_id or "",
            })
            child.insert(ignore_permissions=True)

    def calculate_totals(self):
        total = 0
        for item in self.items:
            item.amount = flt(item.qty) * flt(item.rate)
            total += item.amount
        self.total_amount = total


def run_refresh_stock_transactions(doc_name: str) -> None:
    """Background worker — populates Складські проводки tab after submit commits."""
    frappe.get_doc("Fin Order", doc_name).refresh_stock_transactions()