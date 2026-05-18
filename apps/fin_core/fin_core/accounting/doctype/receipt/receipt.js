frappe.ui.form.on('Receipt', {
    refresh(frm) {
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__('View Realization'), function () {
                frappe.route_options = { receipt_id: frm.doc.name };
                frappe.set_route('query-report', 'Batch Realization');
            }, __('Reports'));
        }

        if (!frm.is_new()) {
            frm.trigger('load_financial_view');
        }
    },

    items_remove(frm) {
        calculate_total(frm);
    },

    load_financial_view(frm) {
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
            callback(r) {
                render_financial_view(frm, r.message || [], 'financial_postings_html');
            },
        });
    },
});

// ── Receipt Item row triggers ────────────────────────────────────────────────

frappe.ui.form.on('Receipt Item', {
    qty(frm, cdt, cdn) {
        calculate_row_amount(frm, cdt, cdn);
    },
    rate(frm, cdt, cdn) {
        calculate_row_amount(frm, cdt, cdn);
    },
});

function calculate_row_amount(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    const amount = flt(row.qty) * flt(row.rate);
    frappe.model.set_value(cdt, cdn, 'amount', amount);
    calculate_total(frm);
}

function calculate_total(frm) {
    const total = (frm.doc.items || []).reduce((sum, row) => sum + flt(row.amount), 0);
    frm.set_value('total_amount', total);
}

// ── Financial View renderer ──────────────────────────────────────────────────

function render_financial_view(frm, rows, field_name) {
    if (!frm.fields_dict[field_name]) return;

    if (!rows.length) {
        frm.get_field(field_name).$wrapper.html(
            '<div style="padding:12px 0;color:#888;">Аналітичних проводок немає. ' +
            'Переконайтесь, що налаштований Transaction Template для Receipt.</div>'
        );
        return;
    }

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
