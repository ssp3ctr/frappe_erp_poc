import frappe
from frappe.model.document import Document

class FinOrderItem(Document):
    """
    Контролер для рядка замовлення.
    Більшість логіки (розрахунок суми, списання)
    знаходиться в батьківському документі Fin Order.
    """
    pass