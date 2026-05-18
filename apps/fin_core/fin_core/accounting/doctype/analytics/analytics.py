import frappe
from frappe.model.document import Document


class Analytics(Document):
    def validate(self):
        self.validate_parent()
        self.clear_unused_fields()

    def validate_parent(self):
        """Перевірка, щоб не призначити батьком самого себе"""
        if self.parent_analytics == self.name:
            frappe.throw(frappe._("Аналітика не може бути батьківською для самої себе"))

        if self.parent_analytics:
            parent = frappe.get_doc("Analytics", self.parent_analytics)
            if parent.type != self.type:
                frappe.throw(frappe._("Тип аналітики ({0}) має збігатися з типом батька ({1})")
                             .format(self.type, parent.type))

    def clear_unused_fields(self):
        """Очищує поле валюти, якщо обрано Quantity, і навпаки"""
        if self.type == "Currency":
            self.uom = None
        else:
            self.currency = None

    def get_balance(self):
        """Метод для швидкого отримання балансу через екземпляр документа"""
        from fin_core.engine.validator import FinValidator
        v = FinValidator({})
        return v._get_current_balance(self.name)