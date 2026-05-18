import frappe
from frappe import _
from frappe.model.document import Document
from fin_core.engine.validator import FinValidator

class FinTransaction(Document):
    def validate(self):
        """
        Викликається перед збереженням (Draft).
        Тут спрацює твій основний валідатор.
        """
        # Створюємо екземпляр твого валідатора
        validator = FinValidator(self)
        validator.validate_all()

    def on_submit(self):
        """
        Викликається після натискання кнопки 'Submit'.
        Саме тут ми фіксуємо зміни в Ledger Entry.
        """
        self.create_ledger_entries()

    def on_cancel(self):
        """
        Викликається при скасуванні транзакції.
        Видаляємо всі пов'язані записи в Ledger, щоб баланс відкотився.
        """
        frappe.db.delete("Ledger Entry", {"finance_transaction": self.name})

    def create_ledger_entries(self):
        """
        Створення подвійного запису в книзі (Ledger Entry).
        """
        # 1. Запис для джерела (Списання)
        # Для джерела ми робимо суму або кількість ВІД'ЄМНОЮ
        self._make_entry(
            analytics=self.from_analytics,
            amount=-abs(float(self.amount or 0)),
            qty=-abs(float(self.qty or 0))
        )

        # 2. Запис для призначення (Прихід)
        # Для цілі ми робимо суму або кількість ДОДАТНОЮ
        self._make_entry(
            analytics=self.to_analytics,
            amount=abs(float(self.amount or 0)),
            qty=abs(float(self.qty or 0))
        )

    def _make_entry(self, analytics, amount, qty):
        """Допоміжний метод для створення одного запису в книзі."""
        if amount == 0 and qty == 0:
            return

        entry = frappe.get_doc({
            "doctype": "Ledger Entry",
            "posting_date": self.date,
            "finance_transaction": self.name,
            "analytics": analytics,
            "amount": amount,
            "qty": qty,
            "reference_doctype": self.reference_doctype,
            "reference_name": self.reference_name
        })
        entry.insert(ignore_permissions=True)