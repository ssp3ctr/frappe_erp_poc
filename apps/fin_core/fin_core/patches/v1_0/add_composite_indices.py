import frappe

def execute():
    # PostgreSQL синтаксис для створення складеного індексу
    try:
        frappe.db.sql("""
            CREATE INDEX IF NOT EXISTS idx_fin_tx_report_performance 
            ON "tabFin Transaction" (date, from_analytics, to_analytics, docstatus)
        """)
    except Exception as e:
        # Логуємо, але не зупиняємо міграцію, якщо індекс уже є
        print(f"Index creation skipped: {str(e)}")