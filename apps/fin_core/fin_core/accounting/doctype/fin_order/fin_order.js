frappe.ui.form.on('Fin Order', {
    refresh: function(frm) {
        frm.set_query('item_analytics', 'items', function() {
            return { filters: { 'type': 'Quantity' } };
        });

        if (!frm.is_new()) {
            frm.trigger('load_financial_view');
        }

        if (frm.fields_dict.audit_tab) {
            $('.form-footer').appendTo(frm.fields_dict.audit_tab.wrapper);
        }

        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__('Trace Stock Sources'), function() {
                frappe.route_options = { source_name: frm.doc.name };
                frappe.set_route('query-report', 'Fin Order Traceability');
            }, __('Reports'));
        }
    },

    load_financial_view: function(frm) {
        frappe.call({
            method: 'frappe.client.get_list',
            args: {
                doctype: 'Analytical Posting',
                filters: {
                    source_doctype: frm.doc.doctype,
                    source_name: frm.doc.name,
                },
                fields: ['analytics_account', 'debit', 'credit', 'posting_date', 'stock_unit', 'rule_name'],
                order_by: 'creation asc',
                limit: 500,
            },
            callback: function(r) {
                render_financial_view(frm, r.message || [], 'transactions_html');
            },
        });
    },
});

frappe.ui.form.on('Fin Order Item', {
    qty: function(frm, cdt, cdn) { calculate_row_amount(frm, cdt, cdn); },
    rate: function(frm, cdt, cdn) { calculate_row_amount(frm, cdt, cdn); },
});

function calculate_row_amount(frm, cdt, cdn) {
    let row = locals[cdt][cdn];
    let amount = flt(row.qty) * flt(row.rate);
    frappe.model.set_value(cdt, cdn, 'amount', amount);
    let total = 0;
    (frm.doc.items || []).forEach(i => { total += flt(i.amount); });
    frm.set_value('total_amount', total);
}

function render_financial_view(frm, rows, field_name) {
    if (!frm.fields_dict[field_name]) return;

    if (!rows.length) {
        frm.get_field(field_name).$wrapper.html(
            '<div style="padding:12px 0;color:#888;">Аналітичних проводок немає.</div>'
        );
        return;
    }

    // Group by analytics_account → sum debit / credit
    const grouped = {};
    rows.forEach(r => {
        if (!grouped[r.analytics_account]) {
            grouped[r.analytics_account] = { debit: 0, credit: 0 };
        }
        grouped[r.analytics_account].debit  += flt(r.debit);
        grouped[r.analytics_account].credit += flt(r.credit);
    });

    let total_debit = 0, total_credit = 0;
    const body_rows = Object.entries(grouped).map(([account, v]) => {
        total_debit  += v.debit;
        total_credit += v.credit;
        return `<tr>
            <td>${account}</td>
            <td style="text-align:right;">${fmt_money(v.debit)}</td>
            <td style="text-align:right;">${fmt_money(v.credit)}</td>
        </tr>`;
    }).join('');

    const balanced = Math.abs(total_debit - total_credit) < 0.01;
    const total_credit_style = balanced ? 'color:#155724;' : 'color:#721c24;font-weight:bold;';
    const balance_banner = (!balanced && rows.length)
        ? `<p style="color:#721c24;margin-top:6px;">⚠ Дебет ≠ Кредит — перевірте правила шаблону</p>`
        : '';

    frm.get_field(field_name).$wrapper.html(`
        <div style="padding:12px 0;">
            <table class="table table-bordered table-condensed"
                   style="background:white;font-size:13px;margin-bottom:4px;">
                <thead>
                    <tr class="text-muted">
                        <th>Рахунок аналітики</th>
                        <th style="text-align:right;width:140px;">Дебет</th>
                        <th style="text-align:right;width:140px;">Кредит</th>
                    </tr>
                </thead>
                <tbody>${body_rows}</tbody>
                <tfoot>
                    <tr style="font-weight:bold;background:#f8f8f8;">
                        <td>Разом</td>
                        <td style="text-align:right;">${fmt_money(total_debit)}</td>
                        <td style="text-align:right;${total_credit_style}">${fmt_money(total_credit)}</td>
                    </tr>
                </tfoot>
            </table>
            <small class="text-muted">${rows.length} проводок · ${Object.keys(grouped).length} рахунків</small>
            ${balance_banner}
        </div>
    `);
}

function fmt_money(val) {
    return frappe.format(val, { fieldtype: 'Currency' });
}
