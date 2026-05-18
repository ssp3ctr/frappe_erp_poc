from dataclasses import dataclass
from decimal import Decimal
from datetime import date
from enum import Enum
from typing import Optional


class TransactionType(Enum):
    RECEIPT = "Receipt"
    CREATE = "Create"
    TRANSFORM = "Transform"
    TRANSFER = "Transfer"
    SPEND = "Spend"
    RESERVE = "Reserve"
    CONSUME_RESERVED = "ConsumeReserved"
    CANCEL_RESERVATION = "CancelReservation"


@dataclass
class TransactionRequest:
    transaction_type: TransactionType
    item_code: str
    warehouse: str
    qty: Decimal
    source_doctype: str
    source_name: str
    target_warehouse: Optional[str] = None
    posting_date: Optional[date] = None
    characteristic: Optional[str] = None
    quality: Optional[str] = None
    supplier: Optional[str] = None
    batch_no: Optional[str] = None
    # For CONSUME_RESERVED: names the Sales Order whose reservation to fulfil.
    reservation_name: Optional[str] = None
    # Monetary value of this transaction line (qty × rate from source document).
    amount: Optional[Decimal] = None
    # Template name for async analytical posting after bulk_save.
    template_name: Optional[str] = None
