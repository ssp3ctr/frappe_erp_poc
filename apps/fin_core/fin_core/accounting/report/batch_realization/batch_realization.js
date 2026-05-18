frappe.query_reports["Batch Realization"] = {

	filters: [
		{
			fieldname: "receipt_id",
			label: __("Receipt"),
			fieldtype: "Link",
			options: "Receipt",
			reqd: 1,
			on_change() {
				frappe.query_report.refresh();
			},
		},
	],

	// ── status colours ───────────────────────────────────────────────────────
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data || value == null) return value ?? "";

		if (column.fieldname === "status") {
			const palette = {
				"Sold":     { bg: "#d4edda", color: "#155724" },
				"Consumed": { bg: "#d4edda", color: "#155724" },
				"Reserved": { bg: "#cce5ff", color: "#004085" },
				"Available":{ bg: "#fff3cd", color: "#856404" },
				"Adjusted": { bg: "#f8d7da", color: "#721c24" },
			};
			const s = palette[data.status];
			if (s) {
				return `<span style="background:${s.bg};color:${s.color};
				               padding:2px 8px;border-radius:10px;font-size:12px;
				               font-weight:600;">${value}</span>`;
			}
		}

		// Clickable link for Fin Order / Sales Order destinations
		if (column.fieldname === "destination" && data._destination_doc && data._destination_type) {
			const routeMap = { "Fin Order": "fin-order", "Sales Order": "sales-order" };
			const slug = routeMap[data._destination_type];
			if (slug) {
				return `<a href="/app/${slug}/${encodeURIComponent(data._destination_doc)}"
				           target="_blank"
				           style="font-weight:600;">${value}</a>`;
			}
		}

		return value ?? "";
	},

	// ── toolbar ──────────────────────────────────────────────────────────────
	onload(report) {
		report.page.add_action_item(__("Open Receipt"), () => {
			const name = frappe.query_report.get_filter_value("receipt_id");
			if (name) frappe.set_route("Form", "Receipt", name);
		});
	},

	// ── totals row label ─────────────────────────────────────────────────────
	get_datatable_options(options) {
		return Object.assign(options, { serialNoColumn: false });
	},
};
