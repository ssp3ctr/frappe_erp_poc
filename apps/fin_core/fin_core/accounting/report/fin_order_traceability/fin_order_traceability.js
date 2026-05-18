frappe.query_reports["Fin Order Traceability"] = {

	filters: [
		{
			fieldname: "source_name",
			label: __("Fin Order"),
			fieldtype: "Link",
			options: "Fin Order",
			reqd: 1,
			on_change() {
				frappe.query_report.refresh();
			},
		},
	],

	// ── visual formatting ────────────────────────────────────────────────────
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		// Bold item-group header rows
		if (data.bold) {
			return `<strong>${value ?? ""}</strong>`;
		}

		// Untraced whole-unit sub-rows
		if (data._untraced && column.fieldname === "source_batch") {
			return `<span style="color:#e67e22;font-style:italic;">${__("Direct (origin untraced)")}</span>`;
		}

		// Receipt link in sub-rows
		if (!data.bold && column.fieldname === "source_batch" && data.source_batch) {
			return `<a href="/app/receipt/${encodeURIComponent(data.source_batch)}"
			           target="_blank"
			           style="color:#1a6496;font-weight:600;">${data.source_batch}</a>`;
		}

		return value ?? "";
	},

	// ── click on item header → open Stock Unit list ──────────────────────────
	get_datatable_options(options) {
		return Object.assign(options, {
			onClick({ column, row }) {
				if (!row) return;
				const data = row.dict || {};
				if (data.bold && data.item_code) {
					frappe.set_route("List", "Stock Unit", { item_code: data.item_code });
				}
			},
		});
	},

	// ── toolbar ──────────────────────────────────────────────────────────────
	onload(report) {
		report.page.add_action_item(__("Open Fin Order"), () => {
			const name = frappe.query_report.get_filter_value("source_name");
			if (name) frappe.set_route("Form", "Fin Order", name);
		});
	},
};
