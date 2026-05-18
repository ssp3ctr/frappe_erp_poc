from dataclasses import dataclass
from decimal import Decimal
from datetime import date, datetime
from typing import Optional


class StockUnitStatus:
    AVAILABLE = "Available"
    RESERVED = "Reserved"
    SOLD = "Sold"
    TRANSFERRED = "Transferred"
    CONSUMED = "Consumed"
    ADJUSTED = "Adjusted"
    TRANSFORMED = "Transformed"

    ACTIVE_STATUSES: frozenset = frozenset({"Available", "Reserved"})


@dataclass
class StockUnit:
    item_code: str
    warehouse: str
    qty: Decimal
    status: str = StockUnitStatus.AVAILABLE
    is_active: bool = True
    name: Optional[str] = None
    parent_unit_id: Optional[str] = None
    source_doctype: Optional[str] = None
    source_name: Optional[str] = None
    characteristic: Optional[str] = None
    quality: Optional[str] = None
    supplier: Optional[str] = None
    batch_no: Optional[str] = None
    posting_date: Optional[date] = None
    creation: Optional[datetime] = None
    reservation_name: Optional[str] = None
    amount: Optional[Decimal] = None

    def deactivate(self, status: str) -> "StockUnit":
        if not self.is_active:
            raise ValueError(f"Unit {self.name} is already inactive")
        self.is_active = False
        self.status = status
        return self

    def reserve(self, reservation_name: str) -> "StockUnit":
        if not self.is_active:
            raise ValueError(f"Unit {self.name} is already inactive")
        if self.status != StockUnitStatus.AVAILABLE:
            raise ValueError(f"Unit {self.name} is not Available (status={self.status})")
        self.status = StockUnitStatus.RESERVED
        self.reservation_name = reservation_name
        return self

    def release_reservation(self) -> "StockUnit":
        if self.status != StockUnitStatus.RESERVED:
            raise ValueError(f"Unit {self.name} is not Reserved (status={self.status})")
        self.status = StockUnitStatus.AVAILABLE
        self.reservation_name = None
        return self
