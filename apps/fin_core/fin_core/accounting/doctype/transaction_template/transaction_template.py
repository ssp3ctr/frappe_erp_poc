import frappe
from frappe.model.document import Document


class TransactionTemplate(Document):
    def validate_analytics(self, source_an_name, target_an_name):
        """
        Перевіряє, чи відповідають обрані аналітики типам, вказаним у шаблоні.
        """
        source_category = frappe.db.get_value("Analytics", source_an_name, "category")
        target_category = frappe.db.get_value("Analytics", target_an_name, "category")

        if source_category != self.source_type:
            frappe.throw(
                frappe._("Джерело '{0}' має тип '{1}', а шаблон вимагає '{2}'")
                .format(source_an_name, source_category, self.source_type)
            )

        if target_category != self.target_type:
            frappe.throw(
                frappe._("Призначення '{0}' має тип '{1}', а шаблон вимагає '{2}'")
                .format(target_an_name, target_category, self.target_type)
            )

        return True