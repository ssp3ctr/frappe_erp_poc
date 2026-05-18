import frappe
from frappe import _
from fin_core.engine.calculator import FinCalculator
from fin_core.engine.uow import UnitOfWork


@frappe.whitelist()
def post_transaction(operation_code, source, target, amount=0, qty=0, reference_doc=None, reference_name=None):
    """
    Публічний API для створення одиночної фінансової операції.
    """
    try:
        with UnitOfWork() as uow:
            calc = FinCalculator(None)  # Якщо немає базового документа, передаємо None

            # Створюємо транзакцію
            tx = calc.prepare_entry(
                operation_code=operation_code,
                amount=frappe.utils.flt(amount),
                qty=frappe.utils.flt(qty),
                source=source,
                target=target
            )

            # Додаємо референс, якщо він переданий (наприклад, ID замовлення)
            if reference_doc and reference_name:
                tx.reference_doctype = reference_doc
                tx.reference_name = reference_name

            uow.register_new(tx)
            uow.commit()

            return {
                "status": "success",
                "transaction_id": tx.name,
                "message": _("Транзакція проведена успішно")
            }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Fin API Error")
        return {
            "status": "error",
            "message": str(e)
        }


@frappe.whitelist()
def batch_post_transactions(operations, reference_doc=None, reference_name=None):
    """
    Пакетне створення транзакцій. Приймає список операцій.
    operations: [
        {"op": "PAY", "from": "wallet_1", "to": "system_fees", "amt": 50},
        {"op": "PAY", "from": "wallet_1", "to": "master_1", "amt": 450}
    ]
    """
    if isinstance(operations, str):
        operations = frappe.parse_json(operations)

    try:
        with UnitOfWork() as uow:
            calc = FinCalculator(None)
            results = []

            for op in operations:
                tx = calc.prepare_entry(
                    operation_code=op.get("op"),
                    amount=op.get("amt", 0),
                    qty=op.get("qty", 0),
                    source=op.get("from"),
                    target=op.get("to")
                )

                if reference_doc and reference_name:
                    tx.reference_doctype = reference_doc
                    tx.reference_name = reference_name

                uow.register_new(tx)
                results.append(tx)

            uow.commit()

            return {
                "status": "success",
                "processed_count": len(results),
                "ids": [d.name for d in results]
            }

    except Exception as e:
        return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET"])
def get_balance(analytics_name):
    """
    Швидке отримання залишку по конкретній аналітиці.
    """
    # Тут можна використовувати метод з нашого Validator
    from fin_core.engine.validator import FinValidator

    # Створюємо пустий об'єкт для доступу до методу (або виносимо метод у utils)
    val = FinValidator({})
    balance = val._get_current_balance(analytics_name)

    return {
        "analytics": analytics_name,
        "balance": balance
    }