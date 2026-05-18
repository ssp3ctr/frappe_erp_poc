frappe.query_reports["Analytics Summary"] = {
    "filters": [
        {
            "fieldname": "from_date",
            "label": __("З дати"),
            "fieldtype": "Date",
            "default": frappe.datetime.month_start(),
            "reqd": 1
        },
        {
            "fieldname": "to_date",
            "label": __("По дату"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 1
        }
    ],

    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (column.fieldname == "closing_balance" && data && data.closing_balance < 0) {
            value = `<span style="color:red; font-weight:bold;">${value}</span>`;
        }
        return value;
    },

    // ВАЖЛИВО: Переносимо сюди для гарантованого спрацювання
    "get_datatable_options": function(options) {
        return Object.assign(options, {
            onClick: ({ column, row }) => {
                if (!row) return;

                const data = row.dict; // В нових версіях DataTable дані лежать в .dict
                const fieldname = column.id || column.fieldname;

                if (fieldname === "analytics" && data && data.analytics) {
                    // Формуємо прості фільтри для надійності
                    frappe.set_route("List", "Fin Transaction", {
                        "from_analytics": data.analytics,
                        "date": [">=", frappe.query_report.get_filter_value("from_date")],
                        // Ми відкриваємо загальний список, зазвичай по одній стороні транзакції
                        // Якщо треба "OR", краще відкривати спеціальний звіт Ledger
                    });
                }
            }
        });
    },

};