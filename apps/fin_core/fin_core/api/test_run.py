import frappe

def run_test():
    frappe.db.rollback() # Чистимо транзакцію для Postgres
    print("Початок тесту...")

    # 1. Спершу створюємо Analytics Type (Категорію), бо Analytics посилається на неї
    if not frappe.db.exists("Analytics Type", "Cash"):
        frappe.get_doc({
            "doctype": "Analytics Type",
            "type_name": "Cash",
            "base_type": "Currency",
            "is_internal": 0
        }).insert(ignore_permissions=True)
        print("Створено тип: Cash")

    if not frappe.db.exists("Analytics Type", "System Account"):
        frappe.get_doc({
            "doctype": "Analytics Type",
            "type_name": "System Account",
            "base_type": "Currency",
            "is_internal": 1
        }).insert(ignore_permissions=True)
        print("Створено тип: System Account")

    # Обов'язково commit, щоб валідація побачила ці записи
    frappe.db.commit()

    # 2. Тепер створюємо самі аналітики
    if not frappe.db.exists("Analytics", "Main Cashbox"):
        frappe.get_doc({
            "doctype": "Analytics",
            "analytics_name": "Main Cashbox",
            "category": "Cash", # Тепер цей лінк валідний
            "type": "Currency",
            "currency": "UAH"
        }).insert(ignore_permissions=True)
        print("Створено аналітику: Main Cashbox")

    # ... далі код транзакції ...
    frappe.db.commit()