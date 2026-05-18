frappe.query_reports["Order Item Source Traceability"] = {

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

		// Bold group-header rows (item summary)
		if (data.bold) {
			value = `<strong>${value ?? ""}</strong>`;
		}

		// Untraced whole-unit rows — muted orange to signal incomplete lineage
		if (data._untraced && column.fieldname === "source_batch") {
			value = `<span style="color:#e67e22;font-style:italic;">${__("Direct (origin untraced)")}</span>`;
		}

		// Highlight Receipt link in sub-rows
		if (!data.bold && column.fieldname === "source_batch" && data.source_batch) {
			value = `<a href="/app/receipt/${encodeURIComponent(data.source_batch)}" target="_blank"
			            style="color:#1a6496;font-weight:600;">${data.source_batch}</a>`;
		}

		return value ?? "";
	},

	// ── open Fin Order on item_code click in header rows ────────────────────
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

	// ── toolbar button: open linked Fin Order ───────────────────────────────
	onload(report) {
		report.page.add_action_item(__("Open Fin Order"), () => {
			const name = frappe.query_report.get_filter_value("source_name");
			if (name) frappe.set_route("Form", "Fin Order", name);
		});
	},
};
