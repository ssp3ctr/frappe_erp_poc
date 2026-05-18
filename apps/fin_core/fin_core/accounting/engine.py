import frappe
from frappe import _
from frappe.utils import flt


def process_document_transactions(doc, method=None):
    """
    Головна точка входу для обробки подій документа (on_submit, on_cancel).
    Підключається через hooks.py
    """
    if doc.docstatus == 1:  # Submitted
        run_submission_logic(doc)
    elif doc.docstatus == 2:  # Cancelled
        run_cancellation_logic(doc)


def run_submission_logic(doc):
    # Очищаємо назву від можливих проксі-об'єктів або пробілів
    doctype_name = str(doc.doctype).strip()

    templates = frappe.get_all("Transaction Template",
                               filters={
                                   "reference_doctype": doctype_name,
                                   "is_disabled": 0
                               },
                               fields=["name"])

    if not templates:
        # Додаємо більш детальний дебаг, щоб побачити, що в базі
        all_templates = frappe.db.get_all("Transaction Template", fields=["name", "reference_doctype"])
        frappe.msgprint(f"Не знайдено шаблону для '{doctype_name}'. В базі є: {all_templates}")
        return

    for t in templates:
        template_doc = frappe.get_doc("Transaction Template", t.name)

        for rule in template_doc.rules:
            if rule.is_per_row:
                # Обробляємо кожну строку в таблиці items
                items = doc.get("items") or []
                for item in items:
                    create_ledger_transaction(rule, item, doc, template_doc.name)
            else:
                # Обробляємо документ як ціле
                create_ledger_transaction(rule, doc, doc, template_doc.name)


def run_cancellation_logic(doc):
    """Знаходить та скасовує всі транзакції, пов'язані з цим документом"""
    linked_transactions = frappe.get_all("Fin Transaction", filters={
        "reference_doctype": doc.doctype,
        "reference_name": doc.name,
        "docstatus": 1
    })

    for tx_info in linked_transactions:
        tx = frappe.get_doc("Fin Transaction", tx_info.name)
        tx.cancel()
        frappe.msgprint(_("Транзакцію {0} скасовано").format(tx.name), alert=True)


def create_ledger_transaction(rule, context, main_doc, template_name):
    """Створює транзакцію на основі динамічних полів суми/кількості"""
    from_an = context.get(rule.source_field)

    if rule.target_type == "Constant":
        to_an = rule.target_value
    else:
        to_an = context.get(rule.target_value)

    if not from_an or not to_an:
        return

    # Динамічне отримання значень з полів, вказаних у темплейті
    amount = 0
    if rule.get("source_amount_field"):
        amount = flt(context.get(rule.source_amount_field))

    qty = 0
    if rule.get("source_qty_field"):
        qty = flt(context.get(rule.source_qty_field))

    # Якщо обидва поля в правилі порожні, транзакція не має сенсу
    if amount == 0 and qty == 0:
        return

    tx = frappe.get_doc({
        "doctype": "Fin Transaction",
        "template": template_name,
        "date": main_doc.get("posting_date") or main_doc.get("creation"),
        "from_analytics": from_an,
        "to_analytics": to_an,
        "amount": amount,
        "qty": qty,
        "reference_doctype": main_doc.doctype,
        "reference_name": main_doc.name
    })

    tx.insert(ignore_permissions=True)
    tx.submit()