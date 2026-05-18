import frappe
from decimal import Decimal
from typing import List

from fin_core.models.stock_unit import StockUnit, StockUnitStatus
from fin_core.models.transaction_request import TransactionRequest, TransactionType
from fin_core.repositories.stock_unit_repository import StockUnitRepository
from fin_core.engine.splitting_service import SplittingService


class UtxoEngine:

    def __init__(
        self,
        repository: StockUnitRepository,
        splitting_service: SplittingService | None = None,
    ):
        self.repository = repository
        self.splitting_service = splitting_service or SplittingService()

    def process(self, request: TransactionRequest) -> None:
        dispatch = {
            TransactionType.RECEIPT:           self._process_receipt,
            TransactionType.SPEND:             self._process_spend,
            TransactionType.RESERVE:           self._process_reserve,
            TransactionType.CONSUME_RESERVED:  self._process_consume_reserved,
            TransactionType.CANCEL_RESERVATION: self._process_cancel_reservation,
        }
        handler = dispatch.get(request.transaction_type)
        if handler is None:
            raise NotImplementedError(
                f"TransactionType.{request.transaction_type.name} is not yet implemented"
            )
        handler(request)

    # ------------------------------------------------------------------

    def _process_receipt(self, request: TransactionRequest) -> None:
        unit = StockUnit(
            item_code=request.item_code,
            warehouse=request.warehouse,
            qty=request.qty,
            amount=request.amount,
            status=StockUnitStatus.AVAILABLE,
            is_active=True,
            source_doctype=request.source_doctype,
            source_name=request.source_name,
            characteristic=request.characteristic,
            quality=request.quality,
            supplier=request.supplier,
            batch_no=request.batch_no,
            posting_date=request.posting_date,
        )
        self.repository.bulk_save([unit])
        self._enqueue_postings([unit.name], request)

    def _process_spend(self, request: TransactionRequest) -> None:
        units = self.repository.find_available(
            request.item_code, request.warehouse, request.qty
        )

        self._assert_sufficient_stock(units, request)

        to_save: List[StockUnit] = []
        consumed: List[StockUnit] = []
        remaining = request.qty

        for unit in units:
            if unit.qty <= remaining:
                unit.source_doctype = request.source_doctype
                unit.source_name = request.source_name
                unit.deactivate(StockUnitStatus.CONSUMED)
                to_save.append(unit)
                consumed.append(unit)
                remaining -= unit.qty
            else:
                # unit.qty > remaining → this is the last unit; split it
                spent, remainder = self.splitting_service.split(unit, remaining)
                # stamp the spending document so on_cancel can find these children
                spent.source_doctype = request.source_doctype
                spent.source_name = request.source_name
                remainder.source_doctype = request.source_doctype
                remainder.source_name = request.source_name
                spent.deactivate(StockUnitStatus.CONSUMED)
                to_save.extend([unit, spent, remainder])
                consumed.append(spent)
                break

        self.repository.bulk_save(to_save)
        self._enqueue_postings([u.name for u in consumed], request)

    def _process_reserve(self, request: TransactionRequest) -> None:
        units = self.repository.find_available(
            request.item_code, request.warehouse, request.qty
        )
        self._assert_sufficient_stock(units, request)

        to_save: List[StockUnit] = []
        remaining = request.qty

        for unit in units:
            if unit.qty <= remaining:
                unit.reserve(request.source_name)
                to_save.append(unit)
                remaining -= unit.qty
            else:
                # Partial reservation — split, keep remainder available
                spent, remainder = self.splitting_service.split(unit, remaining)
                spent.source_doctype = request.source_doctype
                spent.source_name = request.source_name
                spent.status = StockUnitStatus.RESERVED
                spent.is_active = True
                spent.reservation_name = request.source_name
                to_save.extend([unit, spent, remainder])
                break

        self.repository.bulk_save(to_save)
        # Postings for RESERVE are deferred: actual analytical entry is created on CONSUME_RESERVED.

    def _process_consume_reserved(self, request: TransactionRequest) -> None:
        if not request.reservation_name:
            frappe.throw("CONSUME_RESERVED requires reservation_name on the request")

        units = self.repository.find_reserved(request.reservation_name)
        if not units:
            frappe.throw(
                f"No reserved units found for reservation '{request.reservation_name}'. "
                "Cannot consume."
            )

        to_save: List[StockUnit] = []
        for unit in units:
            unit.source_doctype = request.source_doctype
            unit.source_name = request.source_name
            unit.reservation_name = None
            unit.deactivate(StockUnitStatus.SOLD)
            to_save.append(unit)

        self.repository.bulk_save(to_save)
        self._enqueue_postings([u.name for u in to_save], request)

    def _process_cancel_reservation(self, request: TransactionRequest) -> None:
        units = self.repository.find_reserved(request.source_name)
        if not units:
            frappe.throw(
                f"No reserved units found for '{request.source_name}'. "
                "Nothing to cancel."
            )

        to_save: List[StockUnit] = []
        for unit in units:
            unit.release_reservation()
            to_save.append(unit)

        self.repository.bulk_save(to_save)

    def _enqueue_postings(self, unit_names: List[str], request: TransactionRequest) -> None:
        if not unit_names:
            return
        # Skip if no template is configured for this doctype (avoids wasted worker jobs)
        if not request.template_name:
            has_template = frappe.db.exists("Transaction Template", {
                "reference_doctype": request.source_doctype,
                "is_disabled": 0,
            })
            if not has_template:
                return
        frappe.enqueue(
            "fin_core.engine.posting_service.create_postings_from_units",
            queue="long",
            enqueue_after_commit=True,
            unit_names=unit_names,
            template_name=request.template_name,
            source_doctype=request.source_doctype,
            source_name=request.source_name,
            posting_date=str(request.posting_date) if request.posting_date else None,
        )

    def _assert_sufficient_stock(
        self,
        units: List[StockUnit],
        request: TransactionRequest,
    ) -> None:
        available = sum((u.qty for u in units), Decimal("0"))
        if available < request.qty:
            frappe.throw(
                f"Insufficient stock for {request.item_code} at {request.warehouse}: "
                f"need {request.qty}, available {available}"
            )


def get_engine() -> UtxoEngine:
    """Factory for use in DocType on_submit hooks."""
    return UtxoEngine(repository=StockUnitRepository())


def enqueue_utxo_process(doc, method=None):
    """Hook handler — enqueues UTXO processing after the DB transaction commits."""
    frappe.enqueue(
        "fin_core.engine.utxo_engine.run_utxo_process",
        doc_name=doc.name,
        doc_doctype=doc.doctype,
        is_cancelled=(doc.docstatus == 2),
        enqueue_after_commit=True,
        queue="default",
    )


def run_utxo_process(doc_name, doc_doctype, is_cancelled=False):
    """Background worker — re-fetches the doc and delegates to the engine."""
    if is_cancelled:
        return

    doc = frappe.get_doc(doc_doctype, doc_name)

    request = TransactionRequest(
        transaction_type=TransactionType.SPEND,
        item_code=doc.item_code,
        warehouse=doc.warehouse,
        qty=Decimal(str(doc.qty)),
        source_doctype=doc_doctype,
        source_name=doc_name,
    )

    get_engine().process(request)


def run_receipt_process(doc_name: str) -> None:
    """Background worker — creates one TransactionRequest per Receipt line item."""
    if frappe.db.exists("Stock Unit", {"source_doctype": "Receipt", "source_name": doc_name}):
        frappe.logger().info(f"run_receipt_process: {doc_name} already processed — skipping")
        return
    doc = frappe.get_doc("Receipt", doc_name)
    engine = get_engine()

    for item in doc.items:
        qty = Decimal(str(item.qty))
        rate = Decimal(str(item.get("rate") or 0))
        engine.process(TransactionRequest(
            transaction_type=TransactionType.RECEIPT,
            item_code=item.item_analytics,
            warehouse=doc.warehouse,
            qty=qty,
            amount=qty * rate,
            source_doctype="Receipt",
            source_name=doc_name,
            posting_date=doc.posting_date,
        ))
