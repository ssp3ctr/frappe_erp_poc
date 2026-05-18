import frappe

def execute():
    # 1. Перевірка наявності таблиці в БД (безпечніше, ніж перевірка DocType)
    if not frappe.db.table_exists("Wallet"):
        return

    system_wallets = ["Revenue", "Escrow", "Platform Fees"]

    for name in system_wallets:
        # 2. Використовуємо db.exists для швидкості
        if not frappe.db.exists("Wallet", name):
            # 3. Краще використовувати frappe.get_doc().insert() тільки якщо
            # ви впевнені, що всі залежності DocType вже завантажені.
            # Якщо ні — використовуйте frappe.db.insert() для прямого запису в БД.
            try:
                frappe.get_doc({
                    "doctype": "Wallet",
                    "wallet_name": name,
                    "name": name, # Встановлюємо ID вручну для системних записів
                    "is_system": 1,
                    "balance": 0
                }).insert(ignore_permissions=True, ignore_links=True)
            except Exception as e:
                frappe.log_error(f"Failed to create system wallet {name}: {str(e)}")

    # 4. frappe.db.commit() робити НЕ потрібно.
    # Bench migrate сам робить commit після кожного успішного патча.