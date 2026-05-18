import frappe
from frappe.model.document import Document

class AnalyticsType(Document):
    def get_analytics(self):
        """Повертає всі аналітики, що належать до цього типу"""
        return frappe.get_all("Analytics", filters={"category": self.name})

    def validate(self):
        # Можна додати перевірку: якщо ми змінюємо base_type,
        # треба переконатися, що вже створені аналітики не мають транзакцій.
        if not self.is_new():
            old_base_type = frappe.db.get_value("Analytics Type", self.name, "base_type")
            if old_base_type != self.base_type:
                if frappe.db.exists("Fin Transaction", {"operation_config": ["in", self._get_related_ops()]}):
                    frappe.throw(frappe._("Неможливо змінити базовий вимір, оскільки вже існують транзакції."))

    def _get_related_ops(self):
        # Допоміжний метод для пошуку операцій, що використовують цей тип
        return frappe.get_all("Fin Operation Config",
                               filters={"allowed_source_category": self.name},
                               pluck="name")