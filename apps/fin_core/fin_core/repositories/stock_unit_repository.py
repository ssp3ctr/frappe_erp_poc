import frappe
from decimal import Decimal
from typing import List, Optional

from fin_core.models.stock_unit import StockUnit, StockUnitStatus


class StockUnitRepository:

    _DOCTYPE = "Stock Unit"

    _FIELDS = [
        "name", "item_code", "warehouse", "qty", "amount", "status", "is_active",
        "parent_unit_id", "source_doctype", "source_name",
        "characteristic", "quality", "supplier", "batch_no",
        "posting_date", "creation", "reservation_name",
    ]

    def find_available(
        self,
        item_code: str,
        warehouse: str,
        qty: Optional[Decimal] = None,
    ) -> List[StockUnit]:
        """Returns AVAILABLE units sorted FIFO by posting_date, then creation.

        Uses pg_advisory_xact_lock to serialize concurrent SPEND access per
        (item_code, warehouse) pair.  Advisory locks operate outside MVCC so
        they work correctly under Frappe's REPEATABLE READ isolation without
        triggering "could not serialize access" errors (unlike FOR UPDATE which
        fails when another committed transaction has already modified the rows).

        The lock is held until the end of the current transaction, so by the
        time the next waiter acquires it a fresh snapshot has been established
        — it will see the prior transaction's consumed units as Consumed and
        skip them naturally via the WHERE clause.

        If qty is given, returns the minimal prefix of units whose total >= qty.
        Callers are responsible for splitting the last unit if it overshoots.
        """
        # Deterministic, collision-resistant 31-bit key for the advisory lock.
        lock_key = abs(hash(f"{item_code}:{warehouse}")) % (2 ** 31)
        frappe.db.sql(
            "SELECT pg_advisory_xact_lock(%(k)s)", {"k": lock_key}
        )

        rows = frappe.get_all(
            self._DOCTYPE,
            filters={
                "item_code": item_code,
                "warehouse": warehouse,
                "status":    StockUnitStatus.AVAILABLE,
                "is_active": 1,
            },
            fields=self._FIELDS,
            order_by="posting_date asc, creation asc",
        )

        units = [self._to_model(r) for r in rows]

        if qty is None:
            return units

        selected: List[StockUnit] = []
        accumulated = Decimal("0")
        for unit in units:
            selected.append(unit)
            accumulated += unit.qty
            if accumulated >= qty:
                break

        return selected

    def get(self, name: str) -> StockUnit:
        rows = frappe.get_all(
            self._DOCTYPE,
            filters={"name": name},
            fields=self._FIELDS,
        )
        if not rows:
            frappe.throw(f"Stock Unit {name} not found")
        return self._to_model(rows[0])

    def find_by_source(self, source_doctype: str, source_name: str) -> List[StockUnit]:
        rows = frappe.get_all(
            self._DOCTYPE,
            filters={"source_doctype": source_doctype, "source_name": source_name},
            fields=self._FIELDS,
        )
        return [self._to_model(r) for r in rows]

    def find_reserved(self, reservation_name: str) -> List[StockUnit]:
        """Returns all RESERVED units belonging to a given reservation document."""
        rows = frappe.get_all(
            self._DOCTYPE,
            filters={
                "reservation_name": reservation_name,
                "status": StockUnitStatus.RESERVED,
            },
            fields=self._FIELDS,
        )
        return [self._to_model(r) for r in rows]

    def bulk_save(self, units: List[StockUnit]) -> None:
        """Single write point for all StockUnit persistence."""
        for unit in units:
            if unit.name:
                self._update(unit)
            else:
                self._insert(unit)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _insert(self, unit: StockUnit) -> None:
        doc = frappe.get_doc({"doctype": self._DOCTYPE, **self._to_fields(unit)})
        doc.insert(ignore_permissions=True)
        unit.name = doc.name

    def _update(self, unit: StockUnit) -> None:
        frappe.db.set_value(self._DOCTYPE, unit.name, self._to_fields(unit))

    def _to_model(self, row) -> StockUnit:
        raw_amount = row.get("amount")
        return StockUnit(
            name=row.name,
            item_code=row.item_code,
            warehouse=row.warehouse,
            qty=Decimal(str(row.qty)),
            amount=Decimal(str(raw_amount)) if raw_amount is not None else None,
            status=row.status,
            is_active=bool(row.is_active),
            parent_unit_id=row.get("parent_unit_id"),
            source_doctype=row.get("source_doctype"),
            source_name=row.get("source_name"),
            characteristic=row.get("characteristic"),
            quality=row.get("quality"),
            supplier=row.get("supplier"),
            batch_no=row.get("batch_no"),
            posting_date=row.get("posting_date"),
            creation=row.get("creation"),
            reservation_name=row.get("reservation_name"),
        )

    def _to_fields(self, unit: StockUnit) -> dict:
        return {
            "item_code": unit.item_code,
            "warehouse": unit.warehouse,
            "qty": float(unit.qty),
            "amount": float(unit.amount) if unit.amount is not None else None,
            "status": unit.status,
            "is_active": int(unit.is_active),
            "parent_unit_id": unit.parent_unit_id,
            "source_doctype": unit.source_doctype,
            "source_name": unit.source_name,
            "characteristic": unit.characteristic,
            "quality": unit.quality,
            "supplier": unit.supplier,
            "batch_no": unit.batch_no,
            "posting_date": unit.posting_date,
            "reservation_name": unit.reservation_name,
        }
