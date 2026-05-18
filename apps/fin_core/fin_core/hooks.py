app_name = "fin_core"
app_title = "Fin Core"
app_publisher = "Oleksandr Shvets"
app_description = "Financial Core for Services"
app_email = "your-email@example.com"
app_license = "mit"

# Додаємо експорт фікстур, щоб налаштування типів аналітики не зникали
fixtures = [
    {"doctype": "Analytics Type"},
    {"doctype": "Transaction Template"},
    {"doctype": "Transaction Template Rules"},
]

doc_events = {
    "Fin Order": {
        "on_submit": "fin_core.accounting.engine.process_document_transactions",
        "on_cancel": "fin_core.accounting.engine.process_document_transactions"
    },
    "Rashod": {
        "on_submit": "fin_core.engine.utxo_engine.enqueue_utxo_process",
        "on_cancel": "fin_core.engine.utxo_engine.enqueue_utxo_process"
    }
}