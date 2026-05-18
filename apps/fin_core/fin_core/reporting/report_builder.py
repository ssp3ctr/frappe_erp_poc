from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import frappe

from fin_core.models.stock_unit import StockUnitStatus

_SPEND_STATUSES = (StockUnitStatus.CONSUMED, StockUnitStatus.SOLD)

_FIELDS = """
    SELECT COALESCE(SUM(qty), 0)
    FROM `tabStock Unit`
    WHERE item_code = %s
      AND warehouse = %s
"""


@dataclass
class TurnoverReport:
    item_code: str
    warehouse: str
    date_from: date
    date_to: date
    opening_qty: Decimal
    in_qty: Decimal
    out_qty: Decimal
    closing_qty: Decimal


class ReportBuilder:

    def get_turnover_report(
        self,
        item: str,
        warehouse: str,
        date_from: date,
        date_to: date,
    ) -> TurnoverReport:
        """Hybrid report: snapshots for the fast path, live aggregation for the tail.

        Until SnapshotAnchor is implemented the anchor is always None, so
        the entire range is served from live StockUnit aggregation.
        """
        anchor = self._get_anchor_date(item, warehouse)

        if anchor is not None and date_from <= anchor:
            # Snapshot path — not yet implemented
            # fast_data = SnapshotService().query(item, warehouse, date_from, anchor)
            # if date_to > anchor:
            #     live_data = self._live_aggregate(item, warehouse, anchor + timedelta(days=1), date_to)
            # return _merge(fast_data, live_data)
            raise NotImplementedError("Snapshot path requires SnapshotService — not yet implemented")

        return self._live_aggregate(item, warehouse, date_from, date_to)

    # ------------------------------------------------------------------
    # Live aggregation
    # ------------------------------------------------------------------

    def _live_aggregate(
        self,
        item: str,
        warehouse: str,
        date_from: date,
        date_to: date,
    ) -> TurnoverReport:
        opening_qty = self._query_opening_qty(item, warehouse, date_from)
        in_qty = self._query_in_qty(item, warehouse, date_from, date_to)
        out_qty = self._query_out_qty(item, warehouse, date_from, date_to)
        closing_qty = opening_qty + in_qty - out_qty

        return TurnoverReport(
            item_code=item,
            warehouse=warehouse,
            date_from=date_from,
            date_to=date_to,
            opening_qty=opening_qty,
            in_qty=in_qty,
            out_qty=out_qty,
            closing_qty=closing_qty,
        )

    def _query_opening_qty(self, item: str, warehouse: str, date_from: date) -> Decimal:
        # Units active at the START of date_from:
        # created before the period AND still active today OR deactivated on/after date_from.
        # DATE(modified) ≈ deactivated_at: UTXO units are only saved twice (insert + deactivate).
        result = frappe.db.sql(
            """
            SELECT COALESCE(SUM(qty), 0)
            FROM `tabStock Unit`
            WHERE item_code = %s
              AND warehouse = %s
              AND DATE(creation) < %s
              AND (is_active = 1 OR DATE(modified) >= %s)
            """,
            (item, warehouse, date_from, date_from),
        )
        return Decimal(str(result[0][0]))

    def _query_in_qty(
        self, item: str, warehouse: str, date_from: date, date_to: date
    ) -> Decimal:
        # Original receipt units (parent_unit_id IS NULL = not from a split)
        # received during the period.
        result = frappe.db.sql(
            """
            SELECT COALESCE(SUM(qty), 0)
            FROM `tabStock Unit`
            WHERE item_code = %s
              AND warehouse = %s
              AND parent_unit_id IS NULL
              AND DATE(creation) >= %s
              AND DATE(creation) <= %s
            """,
            (item, warehouse, date_from, date_to),
        )
        return Decimal(str(result[0][0]))

    def _query_out_qty(
        self, item: str, warehouse: str, date_from: date, date_to: date
    ) -> Decimal:
        # Consumed/sold units whose deactivation fell inside the period.
        # Uses DATE(modified) instead of DATE(creation) because:
        #   - full consumption: the original unit's modified = time of deactivation
        #   - split consumption: the spent child's modified ≈ creation = time of split
        result = frappe.db.sql(
            """
            SELECT COALESCE(SUM(qty), 0)
            FROM `tabStock Unit`
            WHERE item_code = %s
              AND warehouse = %s
              AND status IN (%s, %s)
              AND DATE(modified) >= %s
              AND DATE(modified) <= %s
            """,
            (item, warehouse, StockUnitStatus.CONSUMED, StockUnitStatus.SOLD, date_from, date_to),
        )
        return Decimal(str(result[0][0]))

    # ------------------------------------------------------------------
    # Anchor — SnapshotAnchor doctype not yet created
    # ------------------------------------------------------------------

    def _get_anchor_date(self, item: str, warehouse: str) -> Optional[date]:
        # When SnapshotAnchor exists, replace with:
        # name = frappe.db.get_value("Snapshot Anchor",
        #     {"item_code": item, "warehouse": warehouse}, "last_valid_date")
        # return name if name else None
        return None
