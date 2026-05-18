from frappe.model.document import Document


class AnalyticsBalance(Document):
    # Analytics Balance is kept as a read-only historical table.
    # All write logic has been removed; the table is no longer updated.
    pass
