import frappe


def run():
    print("--- Початок ініціалізації Fin Core ---")

    # 1. Створюємо типи аналітик (Analytics Type)
    types = [
        {"type_name": "Cash", "base_type": "Currency", "is_internal": 0},
        {"type_name": "Bank", "base_type": "Currency", "is_internal": 0},
        {"type_name": "System Account", "base_type": "Currency", "is_internal": 1},
        {"type_name": "Inventory", "base_type": "Quantity", "is_internal": 0}
    ]

    for t in types:
        if not frappe.db.exists("Analytics Type", t["type_name"]):
            frappe.get_doc({"doctype": "Analytics Type", **t}).insert()
            print(f"Створено тип аналітики: {t['type_name']}")

    # 2. Створюємо базові аналітики (Analytics)
    analytics = [
        {"analytics_name": "Main Cashbox", "category": "Cash", "type": "Currency"},
        {"analytics_name": "Revenue Account", "category": "System Account", "type": "Currency"}
    ]

    for a in analytics:
        if not frappe.db.exists("Analytics", a["analytics_name"]):
            frappe.get_doc({"doctype": "Analytics", **a}).insert()
            print(f"Створено аналітику: {a['analytics_name']}")

    # 3. Створюємо шаблон транзакції (Transaction Template)
    if not frappe.db.exists("Transaction Template", "INTERNAL_TRANSFER"):
        frappe.get_doc({
            "doctype": "Transaction Template",
            "template_name": "Internal Transfer",
            "operation_code": "INTERNAL_TRANSFER",
            "source_type": "Cash",
            "target_type": "Bank",
            "allow_manual_entry": 1
        }).insert()
        print("Створено шаблон операції: INTERNAL_TRANSFER")

    frappe.db.commit()
    print("--- Ініціалізація завершена успішно ---")


if __name__ == "__main__":
    run()