frappe.pages['stock-unit-lineage'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Stock Unit Lineage',
		single_column: true,
	});

	// ── filter bar ────────────────────────────────────────────────────────────
	const $field = page.add_field({
		fieldname: 'stock_unit',
		fieldtype: 'Link',
		options: 'Stock Unit',
		label: 'Stock Unit',
		change() {
			const val = $field.get_value();
			if (val) load(val);
		},
	});

	page.add_button(__('Load'), () => {
		const val = $field.get_value();
		if (val) load(val);
	}, { btn_class: 'btn-primary' });

	// ── main container ────────────────────────────────────────────────────────
	const $body = $('<div class="lineage-body" style="padding:16px;"></div>').appendTo($(wrapper).find('.layout-main-section'));

	// ── pre-fill from route options ───────────────────────────────────────────
	frappe.after_ajax(() => {
		const opts = frappe.route_options || {};
		if (opts.stock_unit) {
			$field.set_value(opts.stock_unit);
			frappe.route_options = {};
			load(opts.stock_unit);
		}
	});

	// ── load & render ─────────────────────────────────────────────────────────
	function load(name) {
		$body.html('<p class="text-muted">Loading…</p>');
		frappe.call({
			method: 'fin_core.accounting.page.stock_unit_lineage.stock_unit_lineage.get_lineage',
			args: { stock_unit: name },
			callback(r) {
				if (!r.message || !r.message.name) {
					$body.html('<p class="text-muted">No data found.</p>');
					return;
				}
				$body.empty();
				$body.append(renderStyles());
				$body.append(renderNode(r.message, 0));
			},
		});
	}

	// ── tree renderer ─────────────────────────────────────────────────────────
	function statusClass(status) {
		const map = {
			'Available':   'su-available',
			'Consumed':    'su-consumed',
			'Sold':        'su-sold',
			'Transferred': 'su-transferred',
			'Adjusted':    'su-adjusted',
			'Transformed': 'su-transformed',
			'Reserved':    'su-reserved',
		};
		return map[status] || 'su-default';
	}

	function renderNode(node, depth) {
		const hasChildren = node.children && node.children.length > 0;
		const $wrap = $('<div class="su-node-wrap"></div>');

		// connector line for non-root nodes
		if (depth > 0) {
			$wrap.css('margin-left', '32px');
		}

		const $card = $(`
			<div class="su-card ${statusClass(node.status)}">
				<div class="su-card-header">
					${hasChildren ? '<span class="su-toggle" title="Toggle children">▾</span>' : '<span class="su-toggle-placeholder"></span>'}
					<a class="su-name" href="/app/stock-unit/${encodeURIComponent(node.name)}" target="_blank">
						${frappe.utils.escape_html(node.name)}
					</a>
					<span class="su-badge">${frappe.utils.escape_html(node.status)}</span>
				</div>
				<div class="su-card-body">
					<span><b>Qty:</b> ${flt(node.qty, 4)}</span>
					<span><b>Item:</b> ${frappe.utils.escape_html(node.item_code || '—')}</span>
					<span><b>WH:</b> ${frappe.utils.escape_html(node.warehouse || '—')}</span>
					<span><b>Date:</b> ${frappe.utils.escape_html(node.posting_date || '—')}</span>
					${node.source_name ? `<span><b>Source:</b> ${frappe.utils.escape_html(node.source_doctype)} / <a href="/app/${encodeURIComponent((node.source_doctype || '').toLowerCase().replace(/ /g, '-'))}/${encodeURIComponent(node.source_name)}" target="_blank">${frappe.utils.escape_html(node.source_name)}</a></span>` : ''}
				</div>
			</div>
		`);

		$wrap.append($card);

		if (hasChildren) {
			const $children = $('<div class="su-children"></div>');
			node.children.forEach(child => {
				$children.append(renderNode(child, depth + 1));
			});
			$wrap.append($children);

			$card.find('.su-toggle').on('click', () => {
				const $t = $card.find('.su-toggle');
				if ($children.is(':visible')) {
					$children.slideUp(150);
					$t.text('▸');
				} else {
					$children.slideDown(150);
					$t.text('▾');
				}
			});
		}

		return $wrap;
	}

	function renderStyles() {
		if ($('#su-lineage-styles').length) return '';
		return `<style id="su-lineage-styles">
			.su-node-wrap { position: relative; }
			.su-node-wrap + .su-node-wrap { margin-top: 6px; }
			.su-children { margin-left: 32px; margin-top: 6px; border-left: 2px solid #e0e0e0; padding-left: 12px; }
			.su-card { border-radius: 6px; padding: 8px 12px; margin-bottom: 4px; border: 1px solid #ddd; background: #fff; }
			.su-card-header { display: flex; align-items: center; gap: 8px; font-weight: 600; }
			.su-card-body { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; color: #555; }
			.su-toggle { cursor: pointer; font-size: 14px; user-select: none; min-width: 14px; }
			.su-toggle-placeholder { min-width: 14px; display: inline-block; }
			.su-badge { font-size: 11px; padding: 1px 6px; border-radius: 10px; background: rgba(0,0,0,0.08); }
			.su-name { font-family: monospace; font-size: 13px; }
			/* status colours */
			.su-available   { border-left: 4px solid #28a745; }
			.su-consumed    { border-left: 4px solid #dc3545; background: #fff5f5; }
			.su-sold        { border-left: 4px solid #007bff; background: #f0f4ff; }
			.su-transferred { border-left: 4px solid #6c757d; background: #f8f9fa; }
			.su-adjusted    { border-left: 4px solid #ffc107; background: #fffdf0; }
			.su-transformed { border-left: 4px solid #17a2b8; background: #f0feff; }
			.su-reserved    { border-left: 4px solid #fd7e14; }
			.su-default     { border-left: 4px solid #ced4da; }
		</style>`;
	}
};
