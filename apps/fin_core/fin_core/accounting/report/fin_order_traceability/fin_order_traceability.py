from fin_core.reporting.traceability.fin_order_traceability import get_columns, get_data


def execute(filters=None):
    return get_columns(), get_data(filters or {})
