import frappe
from frappe import _


def run_transaction_validation(doc, method=None):
    """Викликається для Fin Transaction"""
    if doc.docstatus == 2:
        return
    v = FinValidator(doc)
    v.validate_all()

def validate_analytics(doc, method=None):
    """Викликається для Fin Analytics (вирішує твій AttributeError)"""
    if doc.type == "Currency" and not doc.currency:
        frappe.throw(_("Для грошової аналітики обов'язково вкажіть валюту"))


class FinValidator:
    """
    Валідатор для перевірки фінансових правил перед проведенням транзакцій.
    """

    def __init__(self, transaction_data):
        self.tx = transaction_data  # DocType об'єкт Fin Transaction
        self.config = None
        self.source_an = None
        self.target_an = None

    def validate_all(self):
        """Запуск повного циклу перевірок"""
        self._load_dependencies()
        self._check_required_fields()
        self._check_analytics_compatibility()
        self._check_operation_constraints()
        self._check_balances()

    def _load_dependencies(self):
        # Міняємо operation_config на template (як у твоїм engine.py)
        if not self.tx.get("template"):
            frappe.throw(_("Не вказано конфігурацію операції (Template is missing)."))

        if not self.tx.get("from_analytics") or not self.tx.get("to_analytics"):
            frappe.throw(_("Необхідно вказати обидві аналітики (джерело та ціль)."))

        # Тут також використовуємо template
        self.config = frappe.get_cached_doc("Transaction Template", self.tx.get("template"))
        self.source_an = frappe.get_cached_doc("Analytics", self.tx.get("from_analytics"))
        self.target_an = frappe.get_cached_doc("Analytics", self.tx.get("to_analytics"))

    def _check_required_fields(self):
        """Базова перевірка наявності сум та аналітик"""
        amount = flt(self.tx.get("amount"))
        qty = flt(self.tx.get("qty"))

        #if amount <= 0 and qty <= 0:
        #    frappe.throw(_("Транзакція повинна мати суму або кількість більше нуля."))

    def _check_analytics_compatibility(self):
        """Перевірка сумісності типів (Гроші/ТМЦ) та валют"""
        # Перевірка типу (напр. не можна переказати гроші в "цеглу")
        #if self.source_an.type != self.target_an.type:
        #    frappe.throw(_("Типи аналітик не збігаються: {0} ({1}) -> {2} ({3})")
        #                .format(self.source_an.name, self.source_an.type,
        #                        self.target_an.name, self.target_an.type))

        # Перевірка валюти
        #if self.source_an.type == "Currency":
        #    if self.source_an.currency != self.target_an.currency:
        #        frappe.throw(_("Конфлікт валют: {0} та {1}. Використовуйте обмінну операцію.")
        #                    .format(self.source_an.currency, self.target_an.currency))

    def _check_operation_constraints(self):
        """Перевірка обмежень, заданих у конфігурації операції"""
        if self.config.get("allowed_source_category"):
            if self.source_an.category != self.config.allowed_source_category:
                frappe.throw(_("Операція {0} не дозволена для джерела категорії {1}")
                             .format(self.config.name, self.source_an.category))

        if self.config.get("allowed_target_category"):
            if self.target_an.category != self.config.allowed_target_category:
                frappe.throw(_("Операція {0} не дозволена для цілі категорії {1}")
                             .format(self.config.name, self.target_an.category))

    def _check_balances(self):
        """Перевірка лімітів та від'ємних залишків"""
        # Використовуємо getattr для безпечного доступу до кастомних полів
        if getattr(self.source_an, "disallow_negative_balance", 0):
            current_balance = self._get_current_balance(self.source_an.name)

            # Визначаємо, що саме ми перевіряємо: гроші чи кількість
            requested = flt(self.tx.get("amount")) if self.source_an.type == "Currency" else flt(self.tx.get("qty"))

            if current_balance < requested:
                frappe.throw(_("Недостатньо залишку на {0}. Доступно: {1}, запитано: {2}")
                             .format(self.source_an.name, current_balance, requested))

    def _get_current_balance(self, analytics_name):
        """Розрахунок поточного залишку через SQL для PostgreSQL"""
        # Отримуємо суму всіх вхідних мінус суму всіх вихідних транзакцій
        # Враховуємо тільки зафіксовані (docstatus=1) транзакції

        field = "amount" if self.source_an.type == "Currency" else "qty"

        income = frappe.db.get_value("Fin Transaction",
                                     {"to_analytics": analytics_name, "docstatus": 1},
                                     f"sum({field})") or 0.0

        outcome = frappe.db.get_value("Fin Transaction",
                                      {"from_analytics": analytics_name, "docstatus": 1},
                                      f"sum({field})") or 0.0

        return flt(income) - flt(outcome)


def flt(value):
    """Безпечне перетворення в float"""
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0