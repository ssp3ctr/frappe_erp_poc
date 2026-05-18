import frappe
from frappe import _
from frappe.model.document import Document


class FinCalculator:
    """
    Універсальний двигун для розрахунків на базі конфігурованих аналітик.
    """

    def __init__(self, doc):
        self.doc = doc  # Базовий документ (напр. Накладна, Оплата)
        self.transactions = []

    def validate_operation(self, operation_config, source_analytics, target_analytics):
        """
        Перевіряє, чи відповідає обрана аналітика налаштуванням хоз. операції.
        """
        # 1. Перевірка типів аналітик (Гроші до грошей, Товар до товару)
        if source_analytics.type != target_analytics.type:
            frappe.throw(_("Невідповідність типів аналітик: {0} та {1}")
                         .format(source_analytics.name, target_analytics.name))

        # 2. Перевірка валют/одиниць виміру
        if source_analytics.type == "Currency" and source_analytics.currency != target_analytics.currency:
            frappe.throw(_("Валютна помилка: {0} != {1}")
                         .format(source_analytics.currency, target_analytics.currency))

        if source_analytics.type == "Quantity" and source_analytics.uom != target_analytics.uom:
            # Тут можна додати логіку конвертації одиниць, якщо потрібно
            frappe.throw(_("Помилка одиниць виміру: {0} != {1}")
                         .format(source_analytics.uom, target_analytics.uom))

    def calculate_entry(self, operation_code, amount, qty=0, **kwargs):
        """
        Основний метод створення транзакції.
        operation_code: код з довідника "Хоз. операція"
        """
        # Отримуємо конфігурацію операції
        config = frappe.get_doc("Fin Operation Config", operation_code)

        source_ref = kwargs.get("source_analytics")
        target_ref = kwargs.get("target_analytics")

        if not source_ref or not target_ref:
            frappe.throw(_("Не вказано аналітики для операції {0}").format(operation_code))

        source_an = frappe.get_doc("Fin Analytics", source_ref)
        target_an = frappe.get_doc("Fin Analytics", target_ref)

        # Валідуємо за правилами конфігурації
        self.validate_operation(config, source_an, target_an)

        # Створюємо транзакцію (Fin Transaction)
        transaction = frappe.get_doc({
            "doctype": "Fin Transaction",
            "reference_doctype": self.doc.doctype,
            "reference_name": self.doc.name,
            "operation_config": operation_code,
            "from_analytics": source_an.name,
            "to_analytics": target_an.name,
            "amount": amount,
            "qty": qty,
            "currency": source_an.currency,
            "uom": source_an.uom,
            "posting_date": frappe.utils.today()
        })

        transaction.insert(ignore_permissions=True)
        self.transactions.append(transaction)

        return transaction

    def execute_batch(self, entries):
        """
        Виконує список операцій за один раз.
        entries: list of dicts [{'op': 'SALE', 'amt': 100, ...}]
        """
        for entry in entries:
            self.calculate_entry(
                operation_code=entry.get('op'),
                amount=entry.get('amt'),
                qty=entry.get('qty', 0),
                source_analytics=entry.get('from'),
                target_analytics=entry.get('to')
            )