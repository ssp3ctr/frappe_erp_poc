frappe.ui.form.on('Stock Unit', {
	refresh(frm) {
		frm.add_custom_button(__('View Lineage'), () => {
			frappe.route_options = { stock_unit: frm.doc.name };
			frappe.set_route('stock-unit-lineage');
		});
	},
});
