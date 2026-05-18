from fin_core.reporting.realization.batch_realization import get_columns, get_data


def execute(filters=None):
    return get_columns(), get_data(filters or {})
