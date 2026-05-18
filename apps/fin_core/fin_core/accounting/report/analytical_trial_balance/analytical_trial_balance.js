frappe.query_reports["Analytical Trial Balance"] = {

	filters: [
		{
			fieldname: "from_date",
			label: __("З дати"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("По дату"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "analytics_account",
			label: __("Рахунок аналітики"),
			fieldtype: "Data",
			// autocomplete from existing Analytical Posting records
		},
		{
			fieldname: "source_doctype",
			label: __("Тип документа"),
			fieldtype: "Select",
			options: "\nReceipt\nFin Order\nSales Order",
		},
	],

	// ── column formatter ─────────────────────────────────────────────────────
	formatter(value, row, column, data, default_formatter) {
		if (!data) return default_formatter(value, row, column, data) ?? "";

		value = default_formatter(value, row, column, data);

		// Bold total row
		if (data._is_total) {
			return `<strong>${value ?? ""}</strong>`;
		}

		// Red closing credit when positive (liability / income position)
		if (
			column.fieldname === "closing_credit" &&
			flt(data.closing_credit) > 0
		) {
			return `<span style="color:#721c24;font-weight:600;">${value}</span>`;
		}

		// Dim zero values for readability
		if (
			["opening_debit","opening_credit","period_debit","period_credit",
			 "closing_debit","closing_credit"].includes(column.fieldname) &&
			flt(value) === 0
		) {
			return `<span style="color:#ccc;">–</span>`;
		}

		return value ?? "";
	},

	// ── toolbar ──────────────────────────────────────────────────────────────
	onload(report) {
		report.page.add_action_item(__("Відкрити Analytical Postings"), () => {
			const from_date = frappe.query_report.get_filter_value("from_date");
			const to_date   = frappe.query_report.get_filter_value("to_date");
			const account   = frappe.query_report.get_filter_value("analytics_account");
			const filters   = {};
			if (from_date) filters["posting_date"] = [">=", from_date];
			if (account)   filters["analytics_account"] = account;
			frappe.set_route("List", "Analytical Posting", filters);
		});
	},

	// ── disable built-in total row (we build our own) ────────────────────────
	get_datatable_options(options) {
		return Object.assign(options, { addSerialNoColumn: false });
	},
};
