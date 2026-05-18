from decimal import Decimal

from fin_core.models.stock_unit import StockUnit, StockUnitStatus


class SplittingService:

    def split(self, unit: StockUnit, qty: Decimal) -> tuple[StockUnit, StockUnit]:
        """Split a unit into a spent part and a remainder.

        The source unit is deactivated in-place; two new unsaved units are
        returned. The caller (Engine) is responsible for persisting all three.

        Example:
            unit = StockUnit(item_code="PLYWOOD-18", warehouse="Main", qty=Decimal("10"))
            unit.name = "SU-0001"
            spent, remainder = service.split(unit, Decimal("3"))
            # spent.qty   == Decimal("3"),  spent.parent_unit_id   == "SU-0001"
            # remainder.qty == Decimal("7"), remainder.parent_unit_id == "SU-0001"
            # unit.is_active == False, unit.status == StockUnitStatus.TRANSFERRED

        Args:
            unit: An active StockUnit to split. Must have a persisted name.
            qty:  Quantity to peel off. Must be > 0 and < unit.qty.

        Returns:
            (spent, remainder) — both are new, unsaved StockUnit objects.

        Raises:
            ValueError: if qty is out of range or unit has no name.
        """
        self._validate(unit, qty)

        unit.deactivate(StockUnitStatus.TRANSFERRED)

        spent = self._child(unit, qty)
        remainder = self._child(unit, unit.qty - qty)

        assert spent.qty + remainder.qty == unit.qty, (
            f"Split invariant violated: {spent.qty} + {remainder.qty} != {unit.qty}"
        )

        return spent, remainder

    # ------------------------------------------------------------------

    def _validate(self, unit: StockUnit, qty: Decimal) -> None:
        if not unit.name:
            raise ValueError("Cannot split a unit that has not been saved yet")
        if not unit.is_active:
            raise ValueError(f"Cannot split inactive unit {unit.name}")
        if qty <= Decimal("0"):
            raise ValueError(f"Split qty must be positive, got {qty}")
        if qty >= unit.qty:
            raise ValueError(
                f"Split qty {qty} must be less than unit qty {unit.qty} — "
                "use deactivate() directly for a full consumption"
            )

    def _child(self, source: StockUnit, qty: Decimal) -> StockUnit:
        # Proportional cost: child.amount = parent.amount × (child.qty / parent.qty)
        child_amount = None
        if source.amount is not None and source.qty:
            child_amount = (source.amount * qty / source.qty).quantize(Decimal("0.01"))

        return StockUnit(
            item_code=source.item_code,
            warehouse=source.warehouse,
            qty=qty,
            amount=child_amount,
            status=StockUnitStatus.AVAILABLE,
            is_active=True,
            parent_unit_id=source.name,
            source_doctype=source.source_doctype,
            source_name=source.source_name,
            characteristic=source.characteristic,
            quality=source.quality,
            supplier=source.supplier,
            batch_no=source.batch_no,
            posting_date=source.posting_date,
        )
