import frappe
from frappe import _


class UnitOfWork:
    """
    Паттерн Unit of Work для забезпечення атомарності фінансових операцій.
    Гарантує, що або всі транзакції документа будуть записані, або жодна.
    """

    def __init__(self):
        self.new_objects = []
        self.is_active = False

    def __enter__(self):
        # Починаємо транзакцію в БД
        frappe.db.begin()
        self.is_active = True
        return self

    def register_new(self, doc):
        """Реєструє документ (транзакцію) для збереження"""
        self.new_objects.append(doc)

    def commit(self):
        """Фіксує всі зміни в базі даних"""
        if not self.is_active:
            return

        try:
            for doc in self.new_objects:
                doc.insert(ignore_permissions=True)

            frappe.db.commit()
            self.new_objects = []
        except Exception as e:
            self.rollback()
            raise e
        finally:
            self.is_active = False

    def rollback(self):
        """Відміняє всі зміни в разі помилки"""
        frappe.db.rollback()
        self.new_objects = []
        self.is_active = False
        frappe.log_error(title="UOW Rollback", message=frappe.get_traceback())

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            # Якщо все пройшло успішно, але commit не був викликаний явно
            if self.is_active and self.new_objects:
                self.commit()


def create_user_wallet(doc, method=None):
    """
    Автоматично створює аналітику (гаманець) для нового користувача
    """
    wallet_name = f"Wallet - {doc.email}"

    if not frappe.db.exists("Analytics", wallet_name):
        frappe.get_doc({
            "doctype": "Analytics",
            "analytics_name": wallet_name,
            "category": "Cash",  # Або інша категорія, яку ми створили
            "type": "Currency",
            "currency": "UAH",
            "description": f"Автоматичний гаманець для {doc.full_name}"
        }).insert(ignore_permissions=True)

        # Можна відразу зафіксувати в логах
        frappe.msgprint(f"Створено фінансовий гаманець для {doc.email}")