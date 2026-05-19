/* ============================================================
   Receipt Workspace — receipt-workspace
   ============================================================ */

frappe.pages["receipt-workspace"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Оприбуткування",
		single_column: true,
	});

	const app = new ReceiptWorkspace(page, wrapper);
	wrapper.__receipt_app = app;
};

frappe.pages["receipt-workspace"].on_page_show = function (wrapper) {
	const app = wrapper.__receipt_app;
	if (!app) return;
	const name = frappe.get_route()[1] || new URLSearchParams(location.search).get("name");
	if (name && name !== app.state.doc.name) {
		app.load(name);
	} else if (!name && app.state.doc.name) {
		app.newDoc();
	}
};

/* ============================================================
   ReceiptWorkspace
   ============================================================ */

const API = "fin_core.accounting.page.receipt_workspace.receipt_workspace";

class ReceiptWorkspace {
	constructor(page, wrapper) {
		this.page    = page;
		this.wrapper = wrapper;
		this.$el     = null;

		this.state = {
			doc:        this._emptyDoc(),
			loading:    false,
			saving:     false,
			submitting: false,
			activeTab:  "items",
			postings:   [],
			preview:    { balanced: true, rows: [], total_debit: 0, total_credit: 0 },
			options:    { items: [], suppliers: [], warehouses: [] },
			dirty:      false,
		};

		this._render();
		this._loadOptions();
	}

	// ─── State ──────────────────────────────────────────────────────────

	_emptyDoc() {
		return {
			doctype:            "Receipt",
			name:               null,
			naming_series:      "RCP-.YYYY.-",
			docstatus:          0,
			posting_date:       frappe.datetime.get_today(),
			warehouse:          "",
			customer_analytics: "",
			items:              [],
			total_amount:       0,
		};
	}

	_setState(patch) {
		Object.assign(this.state, patch);
		this._update();
	}

	_patchDoc(patch) {
		Object.assign(this.state.doc, patch);
		this._recalcTotals();
		this.state.dirty = true;
		this._update();
		this._debouncedPreview();
	}

	_recalcTotals() {
		let total = 0;
		for (const row of this.state.doc.items) {
			row.amount  = flt(row.qty) * flt(row.rate);
			total      += row.amount;
		}
		this.state.doc.total_amount = total;
	}

	// ─── Frappe call wrapper ─────────────────────────────────────────────
	// frappe.xcall resolves with r.message even on server exceptions
	// (Frappe returns HTTP 200 + {exc: "...", message: null}).
	// We wrap frappe.call so r.exc causes a real rejection instead of
	// resolving with null, which would crash _update().
	_call(method, args = {}) {
		return new Promise((resolve, reject) => {
			frappe.call({
				method,
				args,
				callback: (r) => {
					if (r && r.exc) {
						// Server exception already displayed by Frappe's cleanup handler
						reject(new Error(r.exc_type || "ServerError"));
					} else {
						resolve(r ? r.message : undefined);
					}
				},
				error: (xhr) => {
					const msg = xhr?.responseJSON?.exception || xhr?.statusText || "Request failed";
					reject(new Error(msg));
				},
			});
		});
	}

	// ─── Data loaders ────────────────────────────────────────────────────

	async _loadOptions() {
		try {
			const [analytics, warehouses] = await Promise.all([
				this._call(`${API}.get_analytics_options`),
				this._call(`${API}.get_warehouse_options`),
			]);
			this._setState({
				options: {
					items:      (analytics || []).filter(a => a.type === "Quantity"),
					suppliers:  (analytics || []).filter(a => a.type === "Currency"),
					warehouses: warehouses || [],
				},
			});
		} catch (e) {
			frappe.show_alert({ message: "Не вдалось завантажити довідники", indicator: "orange" });
		}
	}

	async _loadPostings(name) {
		try {
			const rows = await this._call(`${API}.get_postings`, { source_name: name });
			this._setState({ postings: rows || [] });
		} catch (_) {}
	}

	_debouncedPreview = debounce(async () => {
		if (this.state.doc.docstatus !== 0 || !this.state.doc.items.length) {
			this._setState({ preview: { balanced: true, rows: [], total_debit: 0, total_credit: 0 } });
			return;
		}
		try {
			const p = await this._call(`${API}.get_template_preview`, { doc: this.state.doc });
			if (p) this._setState({ preview: p });
		} catch (_) {}
	}, 500);

	// ─── Payload builder ─────────────────────────────────────────────────
	// Constructs the exact dict structure Frappe expects for insert/save.
	// - Only known Receipt fields are included (total_qty is UI-only)
	// - Child row names included for existing rows, omitted for new rows
	// - doctype always present
	_buildPayload() {
		const d = this.state.doc;
		const payload = {
			doctype:            "Receipt",
			naming_series:      d.naming_series || "RCP-.YYYY.-",
			posting_date:       d.posting_date,
			warehouse:          d.warehouse,
			customer_analytics: d.customer_analytics,
			total_amount:       d.total_amount || 0,
			items: d.items.map(row => {
				const item = {
					item_analytics: row.item_analytics,
					qty:            flt(row.qty),
					rate:           flt(row.rate),
					amount:         flt(row.amount),
				};
				// Keep child-row name only for existing rows (Frappe uses it to update vs insert)
				if (row.name) item.name = row.name;
				return item;
			}),
		};
		// Keep document name only for existing documents
		if (d.name) payload.name = d.name;
		return payload;
	}

	// ─── Document lifecycle ───────────────────────────────────────────────

	async load(name) {
		this._setState({ loading: true });
		try {
			const doc = await this._call(`${API}.get_receipt`, { name });
			if (!doc) throw new Error("Empty response");
			this._setState({
				doc,
				loading:  false,
				dirty:    false,
				postings: [],
				preview:  { balanced: true, rows: [], total_debit: 0, total_credit: 0 },
			});
			if (doc.docstatus === 1) await this._loadPostings(name);
		} catch (e) {
			if (e.message !== "ServerError")
				frappe.msgprint({ title: "Помилка завантаження", message: e.message, indicator: "red" });
			this._setState({ loading: false });
		}
	}

	newDoc() {
		this._setState({
			doc:      this._emptyDoc(),
			postings: [],
			dirty:    false,
			preview:  { balanced: true, rows: [], total_debit: 0, total_credit: 0 },
		});
		frappe.set_route("receipt-workspace");
	}

	async save() {
		if (!this._validate()) return;
		this._setState({ saving: true });
		try {
			const payload = this._buildPayload();
			const doc = await this._call(`${API}.save_receipt`, { doc: payload });
			if (!doc || !doc.name) throw new Error("Сервер повернув порожній результат");
			this._setState({ doc, saving: false, dirty: false });
			frappe.show_alert({ message: `Збережено: ${doc.name}`, indicator: "green" });
			// Update route without triggering a full reload
			if (frappe.get_route()[1] !== doc.name) {
				frappe.set_route("receipt-workspace", doc.name);
			}
		} catch (e) {
			if (e.message !== "ServerError")
				frappe.msgprint({ title: "Помилка збереження", message: e.message, indicator: "red" });
			this._setState({ saving: false });
		}
	}

	async submit() {
		const { doc } = this.state;

		// Allow Save & Submit from new (unsaved) document
		if (!doc.name) {
			if (!this._validate()) return;
			this._setState({ submitting: true });
			try {
				const saved = await this._call(`${API}.save_receipt`, { doc: this._buildPayload() });
				if (!saved || !saved.name) throw new Error("Не вдалось зберегти документ");
				this._setState({ doc: saved, dirty: false });
				frappe.show_alert({ message: `Збережено: ${saved.name}`, indicator: "green" });
			} catch (e) {
				if (e.message !== "ServerError")
					frappe.msgprint({ title: "Помилка", message: e.message, indicator: "red" });
				this._setState({ submitting: false });
				return;
			}
		}

		const ok = await new Promise(res =>
			frappe.confirm("Провести документ?", () => res(true), () => res(false))
		);
		if (!ok) { this._setState({ submitting: false }); return; }

		this._setState({ submitting: true });
		try {
			const doc = await this._call(`${API}.submit_receipt`, { name: this.state.doc.name });
			if (!doc) throw new Error("Порожня відповідь від сервера");
			this._setState({ doc, submitting: false, dirty: false, activeTab: "ledger" });
			await this._loadPostings(doc.name);
			frappe.show_alert({ message: "Документ проведено ✓", indicator: "green" });
		} catch (e) {
			if (e.message !== "ServerError")
				frappe.msgprint({ title: "Помилка проведення", message: e.message, indicator: "red" });
			this._setState({ submitting: false });
		}
	}

	async cancel() {
		if (!this.state.doc.name) return;
		const ok = await new Promise(res =>
			frappe.confirm("Скасувати проведення?", () => res(true), () => res(false))
		);
		if (!ok) return;
		this._setState({ submitting: true });
		try {
			const doc = await this._call(`${API}.cancel_receipt`, { name: this.state.doc.name });
			if (!doc) throw new Error("Порожня відповідь від сервера");
			this._setState({ doc, submitting: false, dirty: false, postings: [] });
			frappe.show_alert({ message: "Документ скасовано", indicator: "orange" });
		} catch (e) {
			if (e.message !== "ServerError")
				frappe.msgprint({ title: "Помилка скасування", message: e.message, indicator: "red" });
			this._setState({ submitting: false });
		}
	}

	_validate() {
		const d = this.state.doc;
		if (!d.warehouse)          { frappe.msgprint("Вкажіть склад");               return false; }
		if (!d.customer_analytics) { frappe.msgprint("Вкажіть постачальника");       return false; }
		if (!d.posting_date)       { frappe.msgprint("Вкажіть дату");                return false; }
		if (!d.items?.length)      { frappe.msgprint("Додайте хоча б одну позицію"); return false; }
		for (let i = 0; i < d.items.length; i++) {
			if (!d.items[i].item_analytics) {
				frappe.msgprint(`Рядок ${i + 1}: оберіть товар`); return false;
			}
			if (flt(d.items[i].qty) <= 0) {
				frappe.msgprint(`Рядок ${i + 1}: кількість має бути більше 0`); return false;
			}
		}
		return true;
	}

	// ─── Items table ──────────────────────────────────────────────────────

	_addRow() {
		const items = [...this.state.doc.items,
			{ name: null, item_analytics: "", qty: 1, rate: 0, amount: 0 }];
		this._patchDoc({ items });
		requestAnimationFrame(() => {
			const sels = this.$el?.querySelectorAll(".item-row-item");
			sels?.[sels.length - 1]?.focus();
		});
	}

	_removeRow(idx) {
		const items = this.state.doc.items.filter((_, i) => i !== idx);
		this._patchDoc({ items });
	}

	_updateRow(idx, field, rawValue) {
		const value = (field === "qty" || field === "rate") ? flt(rawValue) : rawValue;
		const items = this.state.doc.items.map((row, i) =>
			i === idx ? { ...row, [field]: value } : row
		);
		this._patchDoc({ items });
	}

	// ─── Rendering ────────────────────────────────────────────────────────

	_render() {
		const container = document.createElement("div");
		container.className = "rw-root";
		this.page.main.get(0).appendChild(container);
		this.$el = container;

		// Delegated — one handler per event type, attached once forever
		this.$el.addEventListener("click",   e => this._onClick(e));
		this.$el.addEventListener("change",  e => this._onChange(e));
		this.$el.addEventListener("keydown", e => this._onKeyDown(e));

		this._update();
	}

	_update() {
		if (!this.$el) return;

		// Save focused element identity so we can restore focus after innerHTML swap
		const focused    = document.activeElement;
		const focusId    = focused?.id;
		const focusField = focused?.dataset?.field;
		const focusCol   = focused?.dataset?.col;
		const focusRow   = focused?.dataset?.row;

		this.$el.innerHTML = this._html();

		// Restore focus in next microtask (after browser paints the new DOM)
		requestAnimationFrame(() => {
			let el = null;
			if (focusId && !focusField && !focusCol)
				el = this.$el.querySelector(`#${CSS.escape(focusId)}`);
			if (!el && focusField)
				el = this.$el.querySelector(`[data-field="${focusField}"]`);
			if (!el && focusCol != null && focusRow != null)
				el = this.$el.querySelector(`[data-row="${focusRow}"][data-col="${focusCol}"]`);
			if (el) {
				el.focus();
				// Keep cursor at end for text inputs
				if ((el.type === "text" || el.type === "number") && el.setSelectionRange) {
					try { el.setSelectionRange(el.value.length, el.value.length); } catch (_) {}
				}
			}
		});
	}

	// ─── Event handlers ───────────────────────────────────────────────────

	_onClick(e) {
		const actionEl = e.target.closest("[data-action]");
		if (actionEl) {
			switch (actionEl.dataset.action) {
				case "save":       this.save();                                  break;
				case "submit":     this.submit();                                break;
				case "cancel":     this.cancel();                                break;
				case "new":        this.newDoc();                                break;
				case "reload":     this._reload();                               break;
				case "add-row":    this._addRow();                               break;
				case "remove-row": this._removeRow(parseInt(actionEl.dataset.row)); break;
			}
			return;
		}
		const tabEl = e.target.closest("[data-tab]");
		if (tabEl) this._setState({ activeTab: tabEl.dataset.tab });
	}

	_onChange(e) {
		const { field, col, row } = e.target.dataset;
		if (field)                    this._patchDoc({ [field]: e.target.value });
		else if (col != null && row != null) this._updateRow(parseInt(row), col, e.target.value);
	}

	_onKeyDown(e) {
		const { col, row } = e.target.dataset;
		if (col == null || row == null) return;
		const idx = parseInt(row);
		if (e.key === "Enter") {
			e.preventDefault();
			const next = this.$el.querySelector(`[data-row="${idx + 1}"][data-col="${col}"]`);
			if (next) next.focus();
			else if (col === "rate") this._addRow();
		}
		if (e.key === "Delete" && e.altKey) {
			e.preventDefault();
			this._removeRow(idx);
		}
	}

	_reload() {
		if (this.state.doc.name) this.load(this.state.doc.name);
		else this._loadOptions();
	}

	// ─── HTML ─────────────────────────────────────────────────────────────

	_html() {
		const { doc, loading, saving, submitting, activeTab, postings, preview, dirty, options } = this.state;
		const busy        = loading || saving || submitting;
		const isSubmitted = doc.docstatus === 1;
		const isCancelled = doc.docstatus === 2;
		const isDraft     = doc.docstatus === 0;
		const totalQty    = (doc.items || []).reduce((s, r) => s + flt(r.qty), 0);

		return `${this._styles()}
<div style="padding:20px 24px;max-width:1400px;margin:0 auto;">

  <!-- ── HEADER ── -->
  <div class="rw-header">
    <div style="display:flex;align-items:center;gap:14px;min-width:0;">
      <div>
        <div style="font-size:18px;font-weight:700;color:#111827;display:flex;align-items:center;gap:6px;">
          ${esc(doc.name || "Новий документ")}
          ${dirty ? '<span class="dot-dirty" title="Незбережені зміни"></span>' : ""}
        </div>
        <div style="margin-top:3px;">${this._statusBadge(doc.docstatus)}</div>
      </div>
      ${isDraft ? this._balanceIndicator(preview) : ""}
    </div>

    <div style="display:flex;gap:8px;align-items:center;flex-shrink:0;">
      ${busy ? `<span style="font-size:12px;color:#6b7280;">
        ${saving ? "Збереження..." : submitting ? "Проведення..." : "Завантаження..."}
      </span>` : ""}

      <button class="rw-btn rw-btn-ghost" data-action="reload" ${busy ? "disabled" : ""}>↺</button>

      ${isDraft ? `
        <button class="rw-btn rw-btn-primary" data-action="save" ${busy ? "disabled" : ""}>
          💾 Зберегти
        </button>
        <button class="rw-btn rw-btn-success" data-action="submit" ${busy ? "disabled" : ""}>
          ✓ ${doc.name ? "Провести" : "Зберегти і провести"}
        </button>
      ` : ""}
      ${isSubmitted ? `
        <button class="rw-btn rw-btn-danger" data-action="cancel" ${busy ? "disabled" : ""}>
          ✕ Скасувати
        </button>
      ` : ""}

      <button class="rw-btn rw-btn-ghost" data-action="new">+ Новий</button>
    </div>
  </div>

  ${loading ? this._skeletonHTML() : `

  <!-- ── MAIN FORM ── -->
  <div style="display:grid;grid-template-columns:1fr 300px;gap:16px;margin-bottom:16px;">

    <div class="rw-card" style="padding:20px;">
      <div class="rw-section-title">Основні реквізити</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">

        <div>
          <label class="rw-label" for="rw-date">Дата *</label>
          <input id="rw-date" type="date" class="rw-input"
            value="${esc(doc.posting_date || "")}"
            data-field="posting_date"
            ${isCancelled || isSubmitted ? "disabled" : ""}>
        </div>

        <div>
          <label class="rw-label" for="rw-series">Серія</label>
          <input id="rw-series" type="text" class="rw-input"
            value="${esc(doc.naming_series || "RCP-.YYYY.-")}"
            data-field="naming_series"
            ${isCancelled || isSubmitted ? "disabled" : ""}>
        </div>

        <div>
          <label class="rw-label" for="rw-warehouse">Склад *</label>
          <select id="rw-warehouse" class="rw-input"
            data-field="warehouse"
            ${isCancelled || isSubmitted ? "disabled" : ""}>
            <option value="">— Оберіть склад —</option>
            ${options.warehouses.map(w =>
              `<option value="${esc(w.value)}" ${doc.warehouse === w.value ? "selected" : ""}>${esc(w.label)}</option>`
            ).join("")}
          </select>
          ${!options.warehouses.length ? `<div class="rw-hint">Немає складів</div>` : ""}
        </div>

        <div>
          <label class="rw-label" for="rw-supplier">Постачальник *</label>
          <select id="rw-supplier" class="rw-input"
            data-field="customer_analytics"
            ${isCancelled || isSubmitted ? "disabled" : ""}>
            <option value="">— Оберіть постачальника —</option>
            ${options.suppliers.map(a =>
              `<option value="${esc(a.value)}" ${doc.customer_analytics === a.value ? "selected" : ""}>${esc(a.label)}</option>`
            ).join("")}
          </select>
          ${!options.suppliers.length ? `<div class="rw-hint">Немає постачальників (Analytics type=Currency)</div>` : ""}
        </div>

      </div>
    </div>

    <div class="rw-card" style="padding:16px;">
      <div class="rw-section-title">Підсумок</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        ${this._summaryCard("Позиції",  doc.items?.length || 0,       "")}
        ${this._summaryCard("К-ть",     totalQty.toFixed(2),          "")}
        ${this._summaryCard("Сума",     fmt_money(doc.total_amount),  "₴")}
        ${this._summaryCard("Проводок", postings.length,              "")}
      </div>
      ${doc.name ? `
      <div style="margin-top:12px;padding-top:12px;border-top:1px solid #f3f4f6;
        font-size:12px;color:#6b7280;line-height:1.8;">
        <div>📄 ${esc(doc.name)}</div>
        <div>🗓 ${esc(doc.posting_date || "—")}</div>
        <div>🏪 ${esc(doc.warehouse || "—")}</div>
      </div>` : ""}
    </div>

  </div>

  <!-- ── WORKSPACE ── -->
  <div class="rw-card">
    <div class="rw-tab-bar" style="padding:0 16px;">
      <div class="rw-tab ${activeTab === "items"  ? "active" : ""}" data-tab="items">
        📦 Позиції (${doc.items?.length || 0})
      </div>
      <div class="rw-tab ${activeTab === "ledger" ? "active" : ""}" data-tab="ledger">
        📒 Проводки
        ${postings.length ? `<span class="rw-badge-count">${postings.length}</span>` : ""}
      </div>
    </div>
    <div style="padding:16px;">
      ${activeTab === "items"  ? this._itemsTabHTML(doc, isSubmitted || isCancelled) : ""}
      ${activeTab === "ledger" ? this._ledgerTabHTML(doc, postings, preview)         : ""}
    </div>
  </div>

  `}
</div>`;
	}

	// ─── Sub-templates ────────────────────────────────────────────────────

	_styles() {
		return `<style>
.page-content{padding:0!important}
.rw-root{font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",sans-serif}
.rw-root *{box-sizing:border-box}
.rw-header{display:flex;align-items:center;justify-content:space-between;
  margin-bottom:20px;padding:14px 18px;background:#fff;
  border:1px solid #e5e7eb;border-radius:10px;position:sticky;top:60px;
  z-index:100;box-shadow:0 1px 4px rgba(0,0,0,.06);gap:12px}
.rw-input{width:100%;padding:7px 10px;border:1px solid #d1d5db;border-radius:6px;
  font-size:13px;background:#fff;outline:none;transition:border-color .15s;color:#111827}
.rw-input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.12)}
.rw-input:disabled{background:#f9fafb;color:#6b7280;cursor:default}
.rw-label{display:block;font-size:11px;font-weight:600;color:#6b7280;
  text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}
.rw-hint{font-size:11px;color:#f59e0b;margin-top:3px}
.rw-btn{display:inline-flex;align-items:center;gap:5px;padding:7px 14px;border-radius:7px;
  font-size:13px;font-weight:500;border:none;cursor:pointer;transition:all .15s;white-space:nowrap}
.rw-btn:disabled{opacity:.45;cursor:not-allowed}
.rw-btn-ghost{background:transparent;color:#374151;border:1px solid #d1d5db}
.rw-btn-ghost:hover:not(:disabled){background:#f3f4f6}
.rw-btn-primary{background:#2563eb;color:#fff}
.rw-btn-primary:hover:not(:disabled){background:#1d4ed8}
.rw-btn-success{background:#16a34a;color:#fff}
.rw-btn-success:hover:not(:disabled){background:#15803d}
.rw-btn-danger{background:#dc2626;color:#fff}
.rw-btn-danger:hover:not(:disabled){background:#b91c1c}
.rw-card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden}
.rw-section-title{font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;
  letter-spacing:.06em;margin-bottom:14px}
.rw-tab-bar{display:flex;border-bottom:1px solid #e5e7eb}
.rw-tab{padding:10px 18px;font-size:13px;font-weight:500;color:#6b7280;cursor:pointer;
  border-bottom:2px solid transparent;transition:all .15s;user-select:none}
.rw-tab:hover{color:#111827}
.rw-tab.active{color:#2563eb;border-bottom-color:#2563eb}
.rw-badge-count{background:#e0f2fe;color:#0369a1;border-radius:999px;
  padding:1px 7px;font-size:10px;margin-left:4px}
.rw-table{width:100%;border-collapse:collapse;font-size:13px}
.rw-table th{padding:8px 10px;text-align:left;font-size:11px;font-weight:600;color:#6b7280;
  text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #e5e7eb;white-space:nowrap}
.rw-table td{padding:3px 5px;border-bottom:1px solid #f3f4f6;vertical-align:middle}
.rw-table tbody tr:last-child td{border-bottom:none}
.rw-table tbody tr:hover td{background:#f8fafc}
.row-input{width:100%;padding:5px 8px;border:1px solid transparent;border-radius:5px;
  font-size:13px;background:transparent;outline:none;color:#111827}
.row-input:focus{border-color:#3b82f6;background:#fff;box-shadow:0 0 0 3px rgba(59,130,246,.1)}
.row-input.num{text-align:right}
.rw-badge{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;
  border-radius:999px;font-size:11px;font-weight:600}
.badge-draft{background:#f1f5f9;color:#475569}
.badge-submitted{background:#dcfce7;color:#16a34a}
.badge-cancelled{background:#fee2e2;color:#dc2626}
.balance-ok{background:#f0fdf4;border:1px solid #bbf7d0;color:#15803d;
  border-radius:8px;padding:7px 12px;font-size:12px}
.balance-warn{background:#fffbeb;border:1px solid #fde68a;color:#92400e;
  border-radius:8px;padding:7px 12px;font-size:12px}
.summary-card{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px}
.sc-label{font-size:10px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.sc-value{font-size:17px;font-weight:700;color:#111827;margin-top:2px}
.dot-dirty{width:6px;height:6px;border-radius:50%;background:#f59e0b;
  display:inline-block;vertical-align:middle}
.skeleton{background:linear-gradient(90deg,#f0f0f0 25%,#e0e0e0 50%,#f0f0f0 75%);
  background-size:200% 100%;animation:shimmer 1.4s infinite;border-radius:6px}
@keyframes shimmer{0%{background-position:200%}100%{background-position:-200%}}
</style>`;
	}

	_statusBadge(docstatus) {
		if (docstatus === 1) return `<span class="rw-badge badge-submitted">✓ Проведено</span>`;
		if (docstatus === 2) return `<span class="rw-badge badge-cancelled">✕ Скасовано</span>`;
		return `<span class="rw-badge badge-draft">◉ Чернетка</span>`;
	}

	_balanceIndicator(preview) {
		const total = (preview.total_debit || 0) + (preview.total_credit || 0);
		if (!total) return "";
		if (preview.balanced)
			return `<div class="balance-ok">✓ Збалансовано &nbsp;<strong>${fmt_money(preview.total_debit)} = ${fmt_money(preview.total_credit)}</strong></div>`;
		return `<div class="balance-warn">⚠ Дисбаланс &nbsp;Д: <strong>${fmt_money(preview.total_debit)}</strong> К: <strong>${fmt_money(preview.total_credit)}</strong></div>`;
	}

	_summaryCard(label, value, unit) {
		return `<div class="summary-card">
      <div class="sc-label">${label}</div>
      <div class="sc-value">${value}${unit ? `<span style="font-size:11px;color:#9ca3af;margin-left:3px;">${unit}</span>` : ""}</div>
    </div>`;
	}

	_itemsTabHTML(doc, readonly) {
		const items    = doc.items || [];
		const itemOpts = this.state.options.items;

		return `
<div style="overflow-x:auto;">
  <table class="rw-table">
    <thead><tr>
      <th style="width:32px;">#</th>
      <th>Товар (аналітика)</th>
      <th style="width:100px;text-align:right;">Кількість</th>
      <th style="width:130px;text-align:right;">Ціна, ₴</th>
      <th style="width:140px;text-align:right;">Сума, ₴</th>
      ${!readonly ? `<th style="width:36px;"></th>` : ""}
    </tr></thead>
    <tbody>
      ${!items.length ? `
        <tr><td colspan="${readonly ? 5 : 6}"
          style="text-align:center;padding:40px;color:#9ca3af;font-size:13px;">
          ${readonly
            ? "Позицій немає"
            : `Рядків немає — <span style="color:#2563eb;cursor:pointer;"
                data-action="add-row">додати рядок</span>`}
        </td></tr>
      ` : items.map((row, idx) => `
        <tr>
          <td style="text-align:center;color:#9ca3af;font-size:12px;">${idx + 1}</td>
          <td style="min-width:160px;">
            ${readonly
              ? `<span style="padding:5px 8px;">${esc(row.item_analytics || "—")}</span>`
              : `<select class="row-input item-row-item"
                   data-row="${idx}" data-col="item_analytics">
                   <option value="">— Товар —</option>
                   ${itemOpts.map(a =>
                     `<option value="${esc(a.value)}" ${row.item_analytics === a.value ? "selected" : ""}>${esc(a.label)}</option>`
                   ).join("")}
                 </select>`
            }
          </td>
          <td>
            ${readonly
              ? `<span style="float:right;padding:5px 8px;">${flt(row.qty)}</span>`
              : `<input type="number" class="row-input num"
                   data-row="${idx}" data-col="qty"
                   value="${flt(row.qty)}" min="0" step="any">`
            }
          </td>
          <td>
            ${readonly
              ? `<span style="float:right;padding:5px 8px;">${fmt_money(row.rate)}</span>`
              : `<input type="number" class="row-input num"
                   data-row="${idx}" data-col="rate"
                   value="${flt(row.rate)}" min="0" step="any">`
            }
          </td>
          <td style="text-align:right;padding-right:10px;font-weight:500;">
            ${fmt_money(row.amount)}
          </td>
          ${!readonly ? `
          <td style="text-align:center;">
            <button class="rw-btn" style="padding:3px 7px;background:transparent;
              color:#dc2626;border:none;cursor:pointer;font-size:16px;line-height:1;"
              data-action="remove-row" data-row="${idx}" title="Видалити">×</button>
          </td>` : ""}
        </tr>
      `).join("")}
    </tbody>
    ${items.length ? `
    <tfoot><tr style="background:#f8fafc;">
      <td colspan="${readonly ? 4 : 4}"
        style="padding:8px 10px;font-weight:600;text-align:right;color:#374151;">Разом:</td>
      <td style="padding:8px 10px;font-weight:700;font-size:14px;text-align:right;color:#111827;">
        ${fmt_money(doc.total_amount)}
      </td>
      ${!readonly ? `<td></td>` : ""}
    </tr></tfoot>` : ""}
  </table>
</div>
${!readonly ? `
<div style="margin-top:10px;display:flex;align-items:center;gap:12px;">
  <button class="rw-btn rw-btn-ghost" style="font-size:12px;" data-action="add-row">
    + Додати позицію
  </button>
  ${items.length ? `<span style="font-size:11px;color:#9ca3af;">
    Tab між полями · Enter → наступний рядок · Alt+Delete → видалити рядок
  </span>` : ""}
</div>
${!itemOpts.length ? `<div class="rw-hint" style="margin-top:8px;">
  Немає товарів. Додайте Analytics з type=Quantity.
</div>` : ""}` : ""}`;
	}

	_ledgerTabHTML(doc, postings, preview) {
		const isSubmitted = doc.docstatus === 1;
		const rows        = isSubmitted ? postings : preview.rows;
		const isPreview   = !isSubmitted;

		if (!rows?.length) {
			return `<div style="text-align:center;padding:40px;color:#9ca3af;font-size:13px;">
        ${isPreview ? "Заповніть позиції — побачите попередні проводки" : "Проводки відсутні"}
      </div>`;
		}

		let sumD = 0, sumC = 0, sumQ = 0;
		rows.forEach(r => { sumD += flt(r.debit); sumC += flt(r.credit); sumQ += flt(r.qty); });

		return `
${isPreview ? `<div style="margin-bottom:10px;">
  <span style="background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;
    border-radius:6px;padding:4px 10px;font-size:12px;font-weight:500;">
    👁 Попередній перегляд (не збережено)
  </span>
</div>` : ""}
<div style="overflow-x:auto;">
  <table class="rw-table">
    <thead><tr>
      ${!isPreview ? `<th>Правило</th>` : ""}
      <th>Аналітичний рахунок</th>
      <th style="text-align:right;">Дебет, ₴</th>
      <th style="text-align:right;">Кредит, ₴</th>
      <th style="text-align:right;">Кількість</th>
      ${!isPreview ? `<th>Stock Unit</th>` : ""}
    </tr></thead>
    <tbody>
      ${rows.map(r => `
        <tr>
          ${!isPreview ? `<td style="color:#6b7280;font-size:12px;">${esc(r.rule_name || "")}</td>` : ""}
          <td><span style="font-family:monospace;font-size:12px;background:#f1f5f9;
            border-radius:4px;padding:2px 6px;">${esc(r.analytics_account || "")}</span></td>
          <td style="text-align:right;color:${flt(r.debit) ? "#15803d" : "#9ca3af"};
            font-weight:${flt(r.debit) ? "600" : "400"};">
            ${flt(r.debit) ? fmt_money(r.debit) : "—"}
          </td>
          <td style="text-align:right;color:${flt(r.credit) ? "#dc2626" : "#9ca3af"};
            font-weight:${flt(r.credit) ? "600" : "400"};">
            ${flt(r.credit) ? fmt_money(r.credit) : "—"}
          </td>
          <td style="text-align:right;color:#6b7280;font-family:monospace;">
            ${flt(r.qty) ? (r.qty > 0 ? `+${flt(r.qty)}` : flt(r.qty)) : "—"}
          </td>
          ${!isPreview ? `<td style="font-size:11px;color:#9ca3af;font-family:monospace;">
            ${esc((r.stock_unit || "").slice(0, 10))}
          </td>` : ""}
        </tr>
      `).join("")}
    </tbody>
    <tfoot style="border-top:2px solid #e5e7eb;">
      <tr style="background:#f8fafc;font-weight:600;">
        <td colspan="${isPreview ? 1 : 2}" style="padding:8px 10px;font-size:12px;color:#6b7280;">Разом</td>
        <td style="text-align:right;padding:8px 10px;color:#15803d;">${sumD ? fmt_money(sumD) : "—"}</td>
        <td style="text-align:right;padding:8px 10px;color:#dc2626;">${sumC ? fmt_money(sumC) : "—"}</td>
        <td style="text-align:right;padding:8px 10px;font-family:monospace;">${sumQ ? sumQ.toFixed(2) : "—"}</td>
        ${!isPreview ? `<td></td>` : ""}
      </tr>
      ${(sumD || sumC) ? `
      <tr style="background:${Math.abs(sumD - sumC) < 0.01 ? "#f0fdf4" : "#fffbeb"};">
        <td colspan="${isPreview ? 5 : 6}" style="padding:6px 10px;font-size:12px;">
          ${Math.abs(sumD - sumC) < 0.01
            ? `<span style="color:#16a34a;font-weight:600;">✓ Баланс рівний — ${fmt_money(sumD)}</span>`
            : `<span style="color:#d97706;font-weight:600;">⚠ Різниця: ${fmt_money(Math.abs(sumD - sumC))}</span>`}
        </td>
      </tr>` : ""}
    </tfoot>
  </table>
</div>`;
	}

	_skeletonHTML() {
		return `
<div style="display:grid;grid-template-columns:1fr 300px;gap:16px;margin-bottom:16px;">
  <div class="rw-card" style="padding:20px;">
    <div class="skeleton" style="height:14px;width:50%;margin-bottom:18px;"></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
      ${[0,1,2,3].map(() => `<div>
        <div class="skeleton" style="height:11px;width:40%;margin-bottom:6px;"></div>
        <div class="skeleton" style="height:36px;"></div>
      </div>`).join("")}
    </div>
  </div>
  <div class="rw-card" style="padding:16px;">
    <div class="skeleton" style="height:14px;width:50%;margin-bottom:14px;"></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
      ${[0,1,2,3].map(() => `<div class="skeleton" style="height:60px;border-radius:8px;"></div>`).join("")}
    </div>
  </div>
</div>
<div class="rw-card" style="padding:16px;">
  <div class="skeleton" style="height:280px;"></div>
</div>`;
	}
}

/* ============================================================
   Utilities
   ============================================================ */

function flt(v) { const n = parseFloat(v); return isNaN(n) ? 0 : n; }

function fmt_money(value) {
	return flt(value).toLocaleString("uk-UA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function esc(str) {
	return String(str ?? "")
		.replace(/&/g, "&amp;").replace(/</g, "&lt;")
		.replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function debounce(fn, ms) {
	let t;
	return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
