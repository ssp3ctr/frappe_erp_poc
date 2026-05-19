/* ============================================================
   Receipt Workspace — receipt-workspace
   Principal Frontend: Frappe Page + Tailwind CDN + Reactive JS
   ============================================================ */

frappe.pages["receipt-workspace"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Оприбуткування",
		single_column: true,
	});

	// Inject Tailwind CDN once
	if (!document.getElementById("tw-cdn")) {
		const s = document.createElement("script");
		s.id = "tw-cdn";
		s.src = "https://cdn.tailwindcss.com";
		s.onload = () => {
			window.tailwind && tailwind.config({ darkMode: "class" });
		};
		document.head.appendChild(s);
	}

	// Mount the workspace
	const app = new ReceiptWorkspace(page, wrapper);
	wrapper.__receipt_app = app;
};

frappe.pages["receipt-workspace"].on_page_show = function (wrapper) {
	const app = wrapper.__receipt_app;
	if (!app) return;
	// Re-route with ?name=RCP-XXXX from URL
	const name = frappe.get_route()[1] || new URLSearchParams(location.search).get("name");
	if (name) {
		app.load(name);
	} else {
		app.newDoc();
	}
};

/* ============================================================
   Core application class
   ============================================================ */

class ReceiptWorkspace {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$el = null;

		// Reactive state
		this.state = {
			doc: this._emptyDoc(),
			loading: false,
			saving: false,
			submitting: false,
			activeTab: "items",
			postings: [],
			preview: { balanced: true, rows: [], total_debit: 0, total_credit: 0 },
			options: { analytics: [], warehouses: [] },
			dirty: false,
		};

		this._render();
		this._loadOptions();
	}

	// ── State management ───────────────────────────────────────────
	_emptyDoc() {
		return {
			name: null,
			naming_series: "RCP-.YYYY.-",
			docstatus: 0,
			posting_date: frappe.datetime.get_today(),
			warehouse: "",
			customer_analytics: "",
			items: [],
			total_amount: 0,
		};
	}

	_setState(patch) {
		Object.assign(this.state, patch);
		this._update();
	}

	_patchDoc(patch) {
		Object.assign(this.state.doc, patch);
		this._recalcTotals();
		this._setState({ dirty: true });
		this._debouncedPreview();
	}

	_recalcTotals() {
		let total = 0;
		for (const row of this.state.doc.items) {
			row.amount = flt(row.qty) * flt(row.rate);
			total += row.amount;
		}
		this.state.doc.total_amount = total;
	}

	// ── Lifecycle ─────────────────────────────────────────────────
	async load(name) {
		this._setState({ loading: true });
		try {
			const r = await frappe.xcall(
				"fin_core.accounting.page.receipt_workspace.receipt_workspace.get_receipt",
				{ name }
			);
			this._setState({ doc: r, loading: false, dirty: false });
			await this._loadPostings(name);
		} catch (e) {
			frappe.msgprint({ title: "Помилка", message: e.message || e, indicator: "red" });
			this._setState({ loading: false });
		}
	}

	newDoc() {
		this._setState({ doc: this._emptyDoc(), postings: [], dirty: false,
			preview: { balanced: true, rows: [], total_debit: 0, total_credit: 0 } });
	}

	async save() {
		if (!this._validate()) return;
		this._setState({ saving: true });
		try {
			const r = await frappe.xcall(
				"fin_core.accounting.page.receipt_workspace.receipt_workspace.save_receipt",
				{ doc: this.state.doc }
			);
			this._setState({ doc: r, saving: false, dirty: false });
			frappe.show_alert({ message: `Збережено: ${r.name}`, indicator: "green" });
			if (r.name) frappe.set_route("receipt-workspace", r.name);
		} catch (e) {
			frappe.msgprint({ title: "Помилка збереження", message: e.message || e, indicator: "red" });
			this._setState({ saving: false });
		}
	}

	async submit() {
		if (!this.state.doc.name) {
			frappe.msgprint("Спочатку збережіть документ");
			return;
		}
		const confirmed = await new Promise(res =>
			frappe.confirm("Провести документ?", () => res(true), () => res(false))
		);
		if (!confirmed) return;
		this._setState({ submitting: true });
		try {
			const r = await frappe.xcall(
				"fin_core.accounting.page.receipt_workspace.receipt_workspace.submit_receipt",
				{ name: this.state.doc.name }
			);
			this._setState({ doc: r, submitting: false, dirty: false, activeTab: "ledger" });
			await this._loadPostings(r.name);
			frappe.show_alert({ message: "Документ проведено", indicator: "green" });
		} catch (e) {
			frappe.msgprint({ title: "Помилка проведення", message: e.message || e, indicator: "red" });
			this._setState({ submitting: false });
		}
	}

	async cancel() {
		if (!this.state.doc.name) return;
		const confirmed = await new Promise(res =>
			frappe.confirm("Скасувати документ?", () => res(true), () => res(false))
		);
		if (!confirmed) return;
		this._setState({ submitting: true });
		try {
			const r = await frappe.xcall(
				"fin_core.accounting.page.receipt_workspace.receipt_workspace.cancel_receipt",
				{ name: this.state.doc.name }
			);
			this._setState({ doc: r, submitting: false, dirty: false, postings: [] });
			frappe.show_alert({ message: "Документ скасовано", indicator: "orange" });
		} catch (e) {
			frappe.msgprint({ title: "Помилка скасування", message: e.message || e, indicator: "red" });
			this._setState({ submitting: false });
		}
	}

	// ── Data loaders ──────────────────────────────────────────────
	async _loadOptions() {
		const [analytics, warehouses] = await Promise.all([
			frappe.xcall("fin_core.accounting.page.receipt_workspace.receipt_workspace.get_analytics_options"),
			frappe.xcall("fin_core.accounting.page.receipt_workspace.receipt_workspace.get_warehouse_options"),
		]);
		this._setState({ options: { analytics, warehouses } });
	}

	async _loadPostings(name) {
		const rows = await frappe.xcall(
			"fin_core.accounting.page.receipt_workspace.receipt_workspace.get_postings",
			{ source_name: name }
		);
		this._setState({ postings: rows || [] });
	}

	_debouncedPreview = debounce(async () => {
		if (this.state.doc.docstatus !== 0) return;
		try {
			const p = await frappe.xcall(
				"fin_core.accounting.page.receipt_workspace.receipt_workspace.get_template_preview",
				{ doc: this.state.doc }
			);
			this._setState({ preview: p });
		} catch (_) {}
	}, 600);

	_validate() {
		const d = this.state.doc;
		if (!d.warehouse)          { frappe.msgprint("Вкажіть склад");              return false; }
		if (!d.customer_analytics) { frappe.msgprint("Вкажіть постачальника");      return false; }
		if (!d.posting_date)       { frappe.msgprint("Вкажіть дату");               return false; }
		if (!d.items?.length)      { frappe.msgprint("Додайте хоча б одну позицію"); return false; }
		return true;
	}

	// ── Item table helpers ────────────────────────────────────────
	_addRow() {
		const items = [...this.state.doc.items, { name: null, item_analytics: "", qty: 1, rate: 0, amount: 0 }];
		this._patchDoc({ items });
		// Focus the last row's item cell after render
		requestAnimationFrame(() => {
			const inputs = this.$el?.querySelectorAll(".item-row-item");
			if (inputs?.length) inputs[inputs.length - 1].focus();
		});
	}

	_removeRow(idx) {
		const items = this.state.doc.items.filter((_, i) => i !== idx);
		this._patchDoc({ items });
	}

	_updateRow(idx, field, value) {
		const items = this.state.doc.items.map((row, i) =>
			i === idx ? { ...row, [field]: field === "qty" || field === "rate" ? flt(value) : value } : row
		);
		this._patchDoc({ items });
	}

	// ── Rendering ─────────────────────────────────────────────────
	_render() {
		const container = document.createElement("div");
		container.className = "rw-root";
		this.page.main.get(0).appendChild(container);
		this.$el = container;
		this._update();
	}

	_update() {
		if (!this.$el) return;
		const html = this._html();
		// Diff-patch the DOM to preserve focus
		morphdom(this.$el, `<div class="rw-root">${html}</div>`, {
			onBeforeElUpdated: (from, to) => {
				// Don't overwrite focused input value mid-typing
				if (from === document.activeElement && from.tagName === "INPUT") return false;
				return true;
			},
		});
		this._bindEvents();
	}

	// ── HTML template ─────────────────────────────────────────────
	_html() {
		const { doc, loading, saving, submitting, activeTab, postings, preview, dirty } = this.state;
		const busy = loading || saving || submitting;
		const isSubmitted = doc.docstatus === 1;
		const isCancelled = doc.docstatus === 2;
		const isDraft     = doc.docstatus === 0;

		return `
<style>
/* ── Reset Frappe page chrome ── */
.page-content { padding: 0 !important; }
.rw-root { font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif; }
.rw-root * { box-sizing: border-box; }
.rw-input {
  width: 100%; padding: 7px 10px; border: 1px solid #d1d5db; border-radius: 6px;
  font-size: 13px; background: #fff; outline: none; transition: border-color .15s;
}
.rw-input:focus { border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,.12); }
.rw-input:disabled { background: #f9fafb; color: #6b7280; cursor: default; }
.rw-label { display: block; font-size: 11px; font-weight: 600; color: #6b7280;
  text-transform: uppercase; letter-spacing: .05em; margin-bottom: 4px; }
.rw-btn {
  display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px;
  border-radius: 7px; font-size: 13px; font-weight: 500; border: none;
  cursor: pointer; transition: all .15s; white-space: nowrap;
}
.rw-btn:disabled { opacity: .45; cursor: not-allowed; }
.rw-btn-primary  { background: #2563eb; color: #fff; }
.rw-btn-primary:hover:not(:disabled)  { background: #1d4ed8; }
.rw-btn-success  { background: #16a34a; color: #fff; }
.rw-btn-success:hover:not(:disabled)  { background: #15803d; }
.rw-btn-danger   { background: #dc2626; color: #fff; }
.rw-btn-danger:hover:not(:disabled)   { background: #b91c1c; }
.rw-btn-ghost    { background: transparent; color: #374151; border: 1px solid #d1d5db; }
.rw-btn-ghost:hover:not(:disabled)    { background: #f3f4f6; }
.rw-card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden; }
.rw-section-title { font-size: 11px; font-weight: 700; color: #9ca3af;
  text-transform: uppercase; letter-spacing: .06em; margin-bottom: 12px; }
.rw-grid { display: grid; gap: 14px; }
.skeleton { background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
  background-size: 200% 100%; animation: shimmer 1.4s infinite;
  border-radius: 6px; }
@keyframes shimmer { 0%{background-position:200%} 100%{background-position:-200%} }
.rw-tab-bar { display: flex; border-bottom: 1px solid #e5e7eb; }
.rw-tab { padding: 10px 18px; font-size: 13px; font-weight: 500; color: #6b7280;
  cursor: pointer; border-bottom: 2px solid transparent; transition: all .15s; }
.rw-tab:hover { color: #111827; }
.rw-tab.active { color: #2563eb; border-bottom-color: #2563eb; }
.rw-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.rw-table th { padding: 8px 10px; text-align: left; font-size: 11px; font-weight: 600;
  color: #6b7280; text-transform: uppercase; letter-spacing: .04em;
  border-bottom: 1px solid #e5e7eb; white-space: nowrap; }
.rw-table td { padding: 4px 6px; border-bottom: 1px solid #f3f4f6; vertical-align: middle; }
.rw-table tr:last-child td { border-bottom: none; }
.rw-table tr:hover td { background: #f8fafc; }
.item-row-input { width: 100%; padding: 5px 8px; border: 1px solid transparent;
  border-radius: 5px; font-size: 13px; background: transparent; outline: none; }
.item-row-input:focus { border-color: #3b82f6; background: #fff;
  box-shadow: 0 0 0 3px rgba(59,130,246,.1); }
.item-row-input.num { text-align: right; }
.rw-badge { display: inline-flex; align-items: center; gap: 4px; padding: 3px 9px;
  border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge-draft     { background: #f1f5f9; color: #475569; }
.badge-submitted { background: #dcfce7; color: #16a34a; }
.badge-cancelled { background: #fee2e2; color: #dc2626; }
.balance-ok   { background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d;
  border-radius: 8px; padding: 8px 12px; font-size: 12px; }
.balance-warn { background: #fffbeb; border: 1px solid #fde68a; color: #92400e;
  border-radius: 8px; padding: 8px 12px; font-size: 12px; }
.summary-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
  padding: 12px 14px; }
.summary-card .label { font-size: 11px; color: #6b7280; font-weight: 600;
  text-transform: uppercase; letter-spacing: .05em; }
.summary-card .value { font-size: 18px; font-weight: 700; color: #111827; margin-top: 2px; }
.dot-dirty { width: 6px; height: 6px; border-radius: 50%; background: #f59e0b;
  display: inline-block; margin-left: 4px; }
</style>

<div style="padding: 20px 24px; max-width: 1400px; margin: 0 auto;">

  <!-- ═══════════════════════ HEADER BAR ═══════════════════════ -->
  <div style="display:flex; align-items:center; justify-content:space-between;
       margin-bottom: 20px; padding: 14px 18px; background:#fff;
       border:1px solid #e5e7eb; border-radius:10px; position:sticky; top:60px;
       z-index:100; box-shadow:0 1px 4px rgba(0,0,0,.06);">

    <div style="display:flex; align-items:center; gap:12px;">
      <div>
        <span style="font-size:18px; font-weight:700; color:#111827;">
          ${doc.name || "Новий документ"}
          ${dirty ? '<span class="dot-dirty" title="Незбережені зміни"></span>' : ""}
        </span>
        <div style="margin-top:2px;">
          ${this._statusBadge(doc.docstatus)}
        </div>
      </div>

      ${/* Balance indicator */ this._balanceIndicator(preview, doc.docstatus)}
    </div>

    <!-- Action buttons -->
    <div style="display:flex; gap:8px; align-items:center;">
      ${busy ? `<div style="font-size:12px;color:#6b7280;margin-right:8px;">
        <svg class="animate-spin" style="width:14px;height:14px;display:inline;margin-right:4px"
          fill="none" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity=".25"/>
          <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        </svg>${saving ? "Збереження..." : submitting ? "Проведення..." : "Завантаження..."}
      </div>` : ""}

      <button class="rw-btn rw-btn-ghost" data-action="reload"
        ${busy ? "disabled" : ""} tabindex="1">
        ↺ Оновити
      </button>

      ${isDraft ? `
        <button class="rw-btn rw-btn-ghost" data-action="save"
          ${busy ? "disabled" : ""} tabindex="2">
          💾 Зберегти
        </button>
        ${doc.name ? `
          <button class="rw-btn rw-btn-success" data-action="submit"
            ${busy ? "disabled" : ""} tabindex="3">
            ✓ Провести
          </button>
        ` : ""}
      ` : ""}

      ${isSubmitted ? `
        <button class="rw-btn rw-btn-danger" data-action="cancel"
          ${busy ? "disabled" : ""} tabindex="3">
          ✕ Скасувати
        </button>
      ` : ""}

      <button class="rw-btn rw-btn-ghost" data-action="new" tabindex="4">
        + Новий
      </button>
    </div>
  </div>

  ${loading ? this._skeletonHTML() : `

  <!-- ═══════════════════════ MAIN FORM ═══════════════════════ -->
  <div style="display:grid; grid-template-columns:1fr 340px; gap:18px; margin-bottom:18px;">

    <!-- LEFT: Document fields -->
    <div class="rw-card" style="padding:20px;">
      <div class="rw-section-title">Основні реквізити</div>
      <div class="rw-grid" style="grid-template-columns:1fr 1fr; gap:16px;">

        <div>
          <label class="rw-label" for="rw-date">Дата проведення</label>
          <input id="rw-date" type="date" class="rw-input"
            value="${doc.posting_date || ""}"
            ${isSubmitted || isCancelled ? "disabled" : ""}
            data-field="posting_date" tabindex="5">
        </div>

        <div>
          <label class="rw-label" for="rw-series">Серія документа</label>
          <input id="rw-series" type="text" class="rw-input"
            value="${doc.naming_series || "RCP-.YYYY.-"}"
            ${isSubmitted || isCancelled ? "disabled" : ""}
            data-field="naming_series" tabindex="6">
        </div>

        <div>
          <label class="rw-label" for="rw-warehouse">Склад *</label>
          <select id="rw-warehouse" class="rw-input"
            ${isSubmitted || isCancelled ? "disabled" : ""}
            data-field="warehouse" tabindex="7">
            <option value="">— Оберіть склад —</option>
            ${this.state.options.warehouses.map(w =>
              `<option value="${esc(w.value)}" ${doc.warehouse === w.value ? "selected" : ""}>${esc(w.label)}</option>`
            ).join("")}
          </select>
        </div>

        <div>
          <label class="rw-label" for="rw-supplier">Постачальник (розрахунки) *</label>
          <select id="rw-supplier" class="rw-input"
            ${isSubmitted || isCancelled ? "disabled" : ""}
            data-field="customer_analytics" tabindex="8">
            <option value="">— Оберіть контрагента —</option>
            ${this.state.options.analytics.map(a =>
              `<option value="${esc(a.value)}" ${doc.customer_analytics === a.value ? "selected" : ""}>${esc(a.label)}</option>`
            ).join("")}
          </select>
        </div>

      </div>
    </div>

    <!-- RIGHT: Financial summary cards -->
    <div style="display:grid; grid-template-rows:auto 1fr; gap:14px;">

      <div class="rw-card" style="padding:16px;">
        <div class="rw-section-title">Фінансові підсумки</div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
          ${this._summaryCard("Кількість позицій", doc.items?.length || 0, "")}
          ${this._summaryCard("Загальна к-ть", this._totalQty(doc.items), "")}
          ${this._summaryCard("Сума", fmt_money(doc.total_amount), "UAH")}
          ${this._summaryCard("Проводок", this.state.postings.length, "")}
        </div>
      </div>

      ${doc.name ? `
      <div class="rw-card" style="padding:16px; display:flex; flex-direction:column;">
        <div class="rw-section-title">Дії</div>
        <div style="font-size:12px;color:#6b7280;line-height:1.6;">
          <div>📄 ${doc.name}</div>
          <div>🗓 ${doc.posting_date || "—"}</div>
          <div>🏪 ${doc.warehouse || "—"}</div>
        </div>
      </div>` : ""}
    </div>

  </div>

  <!-- ═══════════════════════ BOTTOM WORKSPACE ═══════════════════════ -->
  <div class="rw-card">

    <!-- Tab bar -->
    <div class="rw-tab-bar" style="padding: 0 16px;">
      <div class="rw-tab ${activeTab === "items" ? "active" : ""}"
        data-tab="items">
        📦 Позиції (${doc.items?.length || 0})
      </div>
      <div class="rw-tab ${activeTab === "ledger" ? "active" : ""}"
        data-tab="ledger">
        📒 Бухгалтерські проводки
        ${this.state.postings.length ? `<span style="background:#e0f2fe;color:#0369a1;border-radius:999px;padding:1px 7px;font-size:10px;margin-left:4px;">${this.state.postings.length}</span>` : ""}
      </div>
    </div>

    <div style="padding: 16px;">

      <!-- ── TAB: Items ── -->
      ${activeTab === "items" ? this._itemsTabHTML(doc, isSubmitted || isCancelled) : ""}

      <!-- ── TAB: Ledger ── -->
      ${activeTab === "ledger" ? this._ledgerTabHTML() : ""}

    </div>
  </div>

  `}

</div>`;
	}

	// ── Sub-templates ─────────────────────────────────────────────

	_statusBadge(docstatus) {
		if (docstatus === 1) return `<span class="rw-badge badge-submitted">✓ Проведено</span>`;
		if (docstatus === 2) return `<span class="rw-badge badge-cancelled">✕ Скасовано</span>`;
		return `<span class="rw-badge badge-draft">◉ Чернетка</span>`;
	}

	_balanceIndicator(preview, docstatus) {
		if (docstatus !== 0) return "";
		const total = (preview.total_debit || 0) + (preview.total_credit || 0);
		if (!total) return "";
		if (preview.balanced) {
			return `<div class="balance-ok">
        ✓ Схема збалансована &nbsp; <strong>${fmt_money(preview.total_debit)} = ${fmt_money(preview.total_credit)}</strong>
      </div>`;
		}
		return `<div class="balance-warn">
      ⚠ Схема проводок не збалансована &nbsp;
      Дебет: <strong>${fmt_money(preview.total_debit)}</strong> /
      Кредит: <strong>${fmt_money(preview.total_credit)}</strong>
    </div>`;
	}

	_summaryCard(label, value, unit) {
		return `<div class="summary-card">
      <div class="label">${label}</div>
      <div class="value">${value}${unit ? ` <span style="font-size:11px;color:#9ca3af;">${unit}</span>` : ""}</div>
    </div>`;
	}

	_totalQty(items) {
		return (items || []).reduce((s, r) => s + flt(r.qty), 0).toFixed(2);
	}

	_itemsTabHTML(doc, readonly) {
		const items = doc.items || [];
		return `
<div style="overflow-x:auto;">
  <table class="rw-table">
    <thead>
      <tr>
        <th style="width:36px;">#</th>
        <th>Товар (аналітика)</th>
        <th style="width:110px; text-align:right;">Кількість</th>
        <th style="width:140px; text-align:right;">Ціна, UAH</th>
        <th style="width:150px; text-align:right;">Сума, UAH</th>
        ${!readonly ? `<th style="width:42px;"></th>` : ""}
      </tr>
    </thead>
    <tbody>
      ${items.length ? items.map((row, idx) => this._itemRowHTML(row, idx, readonly)).join("") : `
        <tr><td colspan="${readonly ? 5 : 6}" style="text-align:center;padding:32px;color:#9ca3af;font-size:13px;">
          ${readonly ? "Позиції відсутні" : `Немає позицій — <span style="color:#2563eb;cursor:pointer;" data-action="add-row">додати рядок</span>`}
        </td></tr>
      `}
    </tbody>
    ${items.length ? `
    <tfoot>
      <tr style="background:#f8fafc;">
        <td colspan="${readonly ? 4 : 4}" style="padding:8px 10px; font-weight:600; font-size:13px; text-align:right; color:#374151;">
          Разом:
        </td>
        <td style="padding:8px 10px; font-weight:700; font-size:14px; text-align:right; color:#111827;">
          ${fmt_money(doc.total_amount)}
        </td>
        ${!readonly ? `<td></td>` : ""}
      </tr>
    </tfoot>` : ""}
  </table>
</div>
${!readonly ? `
<div style="margin-top:12px; display:flex; align-items:center; gap:10px;">
  <button class="rw-btn rw-btn-ghost" style="font-size:12px;" data-action="add-row" tabindex="20">
    + Додати позицію
  </button>
  ${items.length ? `<span style="font-size:12px;color:#9ca3af;">
    Tab між полями • Enter для нового рядка • Delete для видалення рядка
  </span>` : ""}
</div>` : ""}`;
	}

	_itemRowHTML(row, idx, readonly) {
		const itemOptions = this.state.options.analytics
			.filter(a => a.type === "Quantity" || !a.type)
			.map(a =>
				`<option value="${esc(a.value)}" ${row.item_analytics === a.value ? "selected" : ""}>${esc(a.label)}</option>`
			).join("");

		const base = 10; // tabindex base for rows
		return `
<tr class="item-row" data-row-idx="${idx}">
  <td style="text-align:center;color:#9ca3af;font-size:12px;padding-left:10px;">${idx + 1}</td>
  <td style="min-width:180px;">
    ${readonly
      ? `<span style="padding:5px 8px;font-size:13px;">${esc(row.item_analytics || "—")}</span>`
      : `<select class="item-row-input item-row-item" data-row="${idx}" data-col="item_analytics"
           tabindex="${base + idx * 3}">
           <option value="">— Товар —</option>${itemOptions}
         </select>`
    }
  </td>
  <td>
    ${readonly
      ? `<span style="padding:5px 8px;font-size:13px;float:right;">${flt(row.qty)}</span>`
      : `<input type="number" class="item-row-input num" data-row="${idx}" data-col="qty"
           value="${flt(row.qty)}" min="0" step="any"
           tabindex="${base + idx * 3 + 1}">`
    }
  </td>
  <td>
    ${readonly
      ? `<span style="padding:5px 8px;font-size:13px;float:right;">${fmt_money(row.rate)}</span>`
      : `<input type="number" class="item-row-input num" data-row="${idx}" data-col="rate"
           value="${flt(row.rate)}" min="0" step="any"
           tabindex="${base + idx * 3 + 2}">`
    }
  </td>
  <td style="text-align:right;padding-right:10px;font-weight:500;color:#111827;">
    ${fmt_money(row.amount)}
  </td>
  ${!readonly ? `
  <td style="text-align:center;">
    <button class="rw-btn" style="padding:4px 8px;background:transparent;color:#dc2626;
      border:none;cursor:pointer;font-size:15px;line-height:1;" data-action="remove-row"
      data-row="${idx}" title="Видалити рядок">×</button>
  </td>` : ""}
</tr>`;
	}

	_ledgerTabHTML() {
		const { postings, preview, state: { doc } } = { postings: this.state.postings, preview: this.state.preview, state: this.state };

		// Use real postings if submitted, preview rows if draft
		const isSubmitted = doc.docstatus === 1;
		const rows = isSubmitted ? postings : preview.rows;
		const isPreview = !isSubmitted;

		if (!rows?.length) {
			return `<div style="text-align:center;padding:40px;color:#9ca3af;font-size:13px;">
        ${isPreview
          ? "Заповніть позиції для перегляду попередніх проводок"
          : "Проводки відсутні — документ не проведено"}
      </div>`;
		}

		// Aggregate totals
		let sumDebit = 0, sumCredit = 0, sumQty = 0;
		rows.forEach(r => { sumDebit += flt(r.debit); sumCredit += flt(r.credit); sumQty += flt(r.qty); });

		return `
${isPreview ? `<div style="margin-bottom:12px;">
  <span style="background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:6px;
    padding:5px 10px;font-size:12px;font-weight:500;">
    👁 Попередній перегляд проводок (не збережено)
  </span>
</div>` : ""}

<div style="overflow-x:auto;">
  <table class="rw-table">
    <thead>
      <tr>
        ${!isPreview ? `<th>Правило</th>` : ""}
        <th>Аналітичний рахунок</th>
        <th style="text-align:right;">Дебет</th>
        <th style="text-align:right;">Кредит</th>
        <th style="text-align:right;">Кількість</th>
        ${!isPreview ? `<th>Stock Unit</th>` : ""}
      </tr>
    </thead>
    <tbody>
      ${rows.map(r => `
        <tr>
          ${!isPreview ? `<td style="color:#6b7280;font-size:12px;">${esc(r.rule_name || "")}</td>` : ""}
          <td>
            <span style="font-family:monospace;font-size:12px;background:#f1f5f9;
              border-radius:4px;padding:2px 6px;">${esc(r.analytics_account || "")}</span>
          </td>
          <td style="text-align:right;color:${flt(r.debit) ? "#15803d" : "#9ca3af"};font-weight:${flt(r.debit) ? "600" : "400"};">
            ${flt(r.debit) ? fmt_money(r.debit) : "—"}
          </td>
          <td style="text-align:right;color:${flt(r.credit) ? "#dc2626" : "#9ca3af"};font-weight:${flt(r.credit) ? "600" : "400"};">
            ${flt(r.credit) ? fmt_money(r.credit) : "—"}
          </td>
          <td style="text-align:right;color:#6b7280;font-family:monospace;">
            ${flt(r.qty) ? r.qty > 0 ? `+${flt(r.qty)}` : flt(r.qty) : "—"}
          </td>
          ${!isPreview ? `<td style="font-size:11px;color:#9ca3af;font-family:monospace;">${esc((r.stock_unit || "").slice(0, 10))}</td>` : ""}
        </tr>
      `).join("")}
    </tbody>
    <tfoot style="border-top:2px solid #e5e7eb;">
      <tr style="background:#f8fafc;font-weight:600;">
        <td colspan="${isPreview ? 1 : 2}" style="padding:8px 10px;font-size:12px;color:#6b7280;">Разом</td>
        <td style="text-align:right;padding:8px 10px;color:#15803d;">${sumDebit ? fmt_money(sumDebit) : "—"}</td>
        <td style="text-align:right;padding:8px 10px;color:#dc2626;">${sumCredit ? fmt_money(sumCredit) : "—"}</td>
        <td style="text-align:right;padding:8px 10px;font-family:monospace;">${sumQty ? sumQty.toFixed(2) : "—"}</td>
        ${!isPreview ? `<td></td>` : ""}
      </tr>
      ${(sumDebit || sumCredit) ? `
      <tr style="background:${Math.abs(sumDebit - sumCredit) < 0.01 ? "#f0fdf4" : "#fffbeb"};">
        <td colspan="${isPreview ? 5 : 6}" style="padding:6px 10px;font-size:12px;">
          ${Math.abs(sumDebit - sumCredit) < 0.01
            ? `<span style="color:#16a34a;font-weight:600;">✓ Баланс рівний — ${fmt_money(sumDebit)}</span>`
            : `<span style="color:#d97706;font-weight:600;">⚠ Різниця: ${fmt_money(Math.abs(sumDebit - sumCredit))}</span>`
          }
        </td>
      </tr>` : ""}
    </tfoot>
  </table>
</div>`;
	}

	_skeletonHTML() {
		return `<div style="display:grid;grid-template-columns:1fr 340px;gap:18px;margin-bottom:18px;">
      <div class="rw-card" style="padding:20px;">
        ${[1,2].map(() => `
          <div class="skeleton" style="height:20px;margin-bottom:16px;width:60%;"></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
            ${[1,2,3,4].map(() => `
              <div>
                <div class="skeleton" style="height:12px;width:40%;margin-bottom:6px;"></div>
                <div class="skeleton" style="height:36px;"></div>
              </div>`).join("")}
          </div>`).join("")}
      </div>
      <div>
        ${[1,2,3,4].map(() => `
          <div class="skeleton" style="height:72px;margin-bottom:10px;border-radius:8px;"></div>`).join("")}
      </div>
    </div>
    <div class="rw-card" style="padding:16px;">
      <div class="skeleton" style="height:300px;"></div>
    </div>`;
	}

	// ── Event binding ─────────────────────────────────────────────
	_bindEvents() {
		if (!this.$el) return;

		// Action buttons
		this.$el.querySelectorAll("[data-action]").forEach(el => {
			el.addEventListener("click", e => {
				e.stopPropagation();
				const action = el.dataset.action;
				switch (action) {
					case "save":      this.save();    break;
					case "submit":    this.submit();  break;
					case "cancel":    this.cancel();  break;
					case "new":       this.newDoc();  break;
					case "reload":    this._reload(); break;
					case "add-row":   this._addRow(); break;
					case "remove-row":
						this._removeRow(parseInt(el.dataset.row)); break;
				}
			}, { once: true });
		});

		// Top-level field changes
		this.$el.querySelectorAll("[data-field]").forEach(el => {
			el.addEventListener("change", () => {
				this._patchDoc({ [el.dataset.field]: el.value });
			});
			el.addEventListener("input", () => {
				this._patchDoc({ [el.dataset.field]: el.value });
			});
		});

		// Item row inputs
		this.$el.querySelectorAll("[data-col]").forEach(el => {
			el.addEventListener("change", () => {
				const idx = parseInt(el.dataset.row);
				this._updateRow(idx, el.dataset.col, el.value);
			});

			// Enter → move to next row's same column or add row
			if (el.tagName === "INPUT") {
				el.addEventListener("keydown", e => {
					if (e.key === "Enter") {
						e.preventDefault();
						const idx = parseInt(el.dataset.row);
						const col = el.dataset.col;
						const nextRow = this.$el.querySelector(
							`[data-row="${idx + 1}"][data-col="${col}"]`
						);
						if (nextRow) {
							nextRow.focus();
						} else if (col === "rate") {
							this._addRow();
						}
					}
					if (e.key === "Delete" && e.altKey) {
						this._removeRow(parseInt(el.dataset.row));
					}
				});
			}
		});

		// Tab switching
		this.$el.querySelectorAll("[data-tab]").forEach(el => {
			el.addEventListener("click", () => {
				this._setState({ activeTab: el.dataset.tab });
			}, { once: true });
		});
	}

	_reload() {
		if (this.state.doc.name) {
			this.load(this.state.doc.name);
		} else {
			this._loadOptions();
		}
	}
}

/* ============================================================
   Utilities
   ============================================================ */

function flt(v, precision = 6) {
	const n = parseFloat(v);
	return isNaN(n) ? 0 : n;
}

function fmt_money(value, currency = "") {
	const n = flt(value, 2);
	return n.toLocaleString("uk-UA", {
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	}) + (currency ? ` ${currency}` : "");
}

function esc(str) {
	return String(str ?? "")
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;");
}

function debounce(fn, ms) {
	let t;
	return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/* Lightweight DOM morphing — avoids full re-render flicker */
function morphdom(from, toHTML, opts = {}) {
	const tmp = document.createElement("div");
	tmp.innerHTML = toHTML;
	const to = tmp.firstChild;
	_morphEl(from, to, opts);
}

function _morphEl(from, to, opts) {
	if (!to) { from.parentNode?.removeChild(from); return; }
	if (from.nodeType === 3) { if (from.textContent !== to.textContent) from.textContent = to.textContent; return; }
	if (from.tagName !== to.tagName) { from.parentNode?.replaceChild(to, from); return; }

	// Attributes
	for (const attr of Array.from(to.attributes)) {
		if (from.getAttribute(attr.name) !== attr.value) from.setAttribute(attr.name, attr.value);
	}
	for (const attr of Array.from(from.attributes)) {
		if (!to.hasAttribute(attr.name)) from.removeAttribute(attr.name);
	}

	// Allow caller to skip updating specific elements
	if (opts.onBeforeElUpdated && opts.onBeforeElUpdated(from, to) === false) return;

	// Children
	const fromChildren = Array.from(from.childNodes);
	const toChildren   = Array.from(to.childNodes);
	const maxLen = Math.max(fromChildren.length, toChildren.length);
	for (let i = 0; i < maxLen; i++) {
		const fc = fromChildren[i];
		const tc = toChildren[i];
		if (!fc) { from.appendChild(tc); }
		else if (!tc) { from.removeChild(fc); }
		else { _morphEl(fc, tc, opts); }
	}
}
