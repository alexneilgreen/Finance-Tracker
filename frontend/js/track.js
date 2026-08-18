/* =========================================================================
   track.js — Page 2: Track (three sub-tabs)
   ========================================================================= */

const Track = {
  lineItemsForMonth: [],
  accountsChart: null,
  accountsChartYear: null,
  lastAccounts: [],
  selectedTxIds: new Set(),

  async refresh() {
    if (document.getElementById("page-track").classList.contains("active") === false) return;
    await this.refreshLedger();
  },

  /* =======================================================================
     TAB A — Daily Ledger
     ======================================================================= */
  async refreshLedger() {
    this.lineItemsForMonth = await apiGet(`/api/budget/line_items?month=${App.currentMonth}`);
    this.selectedTxIds.clear();
    this.populateLineItemSelect();
    await this.renderLedgerSummary();
    await this.renderTransactions();
    await this.renderPendingCredits();
    this.bindLedgerForm();
    this.bindImportForm();
    this.bindBulkToolbar();
  },

  /* ---- Pending Credits (Credit-type import rows awaiting fund routing) ---- */
  async renderPendingCredits() {
    const credits = await apiGet("/api/pending_credits");
    const card = document.getElementById("pending-credits-card");
    const wrap = document.getElementById("pending-credits-wrap");

    // Not month-scoped like the rest of the Daily Ledger tab — a Credit
    // row sits here until it's actioned, regardless of which Ledger Month
    // happens to be selected, so nothing gets lost behind the month picker.
    if (!credits.length) {
      card.style.display = "none";
      return;
    }
    card.style.display = "";

    const funds = await apiGet("/api/sinking_funds");

    // Budget categories are month-scoped, but pending credits can span many
    // months (an import can cover a CSV's whole date range) — fetch each
    // distinct month's line items once rather than once per credit row.
    const months = [...new Set(credits.map((c) => c.date.slice(0, 7)))];
    const lineItemsByMonth = {};
    await Promise.all(months.map(async (m) => {
      lineItemsByMonth[m] = await apiGet(`/api/budget/line_items?month=${m}`);
    }));

    wrap.innerHTML = credits.map((c) => {
      const month = c.date.slice(0, 7);
      const lineItems = lineItemsByMonth[month] || [];
      const fundOptions = funds.map((f) => `<option value="fund-${f.id}">Fund: ${f.name}</option>`).join("");
      const categoryOptions = lineItems.map((li) =>
        `<option value="item-${li.id}">Category: ${li.group_name} &rsaquo; ${li.name}</option>`
      ).join("");
      const hasOptions = funds.length || lineItems.length;
      const destOptions = hasOptions
        ? `<option value="">Choose destination&hellip;</option>${fundOptions}${categoryOptions}`
        : `<option value="">No funds or ${month} categories yet</option>`;

      return `
        <div class="credit-review-row" data-id="${c.id}" data-month="${month}">
          <span class="credit-date">${c.date}</span>
          <span class="credit-desc">${c.description || ""}</span>
          <span class="num credit-amount">${formatCurrency(c.amount)}</span>
          <select class="credit-dest-select">${destOptions}</select>
          <button class="btn credit-assign-btn" ${hasOptions ? "" : "disabled"}>Add</button>
          <button class="btn-ghost credit-dismiss-btn">Dismiss</button>
        </div>
      `;
    }).join("");

    wrap.querySelectorAll(".credit-assign-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const row = btn.closest(".credit-review-row");
        const dest = row.querySelector(".credit-dest-select").value;
        if (!dest) { alert("Choose a Fund or Budget Category first."); return; }
        const [kind, idRaw] = dest.split("-");
        const targetId = parseInt(idRaw, 10);

        btn.disabled = true;
        try {
          if (kind === "fund") {
            await apiPost(`/api/pending_credits/${row.dataset.id}/assign_to_fund`, { fund_id: targetId });
            await this.refreshFunds();
          } else {
            await apiPost(`/api/pending_credits/${row.dataset.id}/assign_to_line_item`, { line_item_id: targetId });
            // Only refresh the ledger tables if the credit's own month
            // matches what's currently on screen -- otherwise leave the
            // visible month's tables untouched.
            if (row.dataset.month === App.currentMonth) {
              await this.renderLedgerSummary();
              await this.renderTransactions();
            }
          }
          await this.renderPendingCredits();
        } catch (err) {
          alert(`Couldn't add that: ${err.message}`);
          btn.disabled = false;
        }
      });
    });

    wrap.querySelectorAll(".credit-dismiss-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Dismiss this credit without logging it anywhere? This can't be undone.")) return;
        const row = btn.closest(".credit-review-row");
        await apiDelete(`/api/pending_credits/${row.dataset.id}`);
        await this.renderPendingCredits();
      });
    });
  },

  populateLineItemSelect() {
    const select = document.getElementById("line-item-select");
    select.innerHTML = this.lineItemsForMonth
      .map((li) => `<option value="${li.id}">${li.group_name} &rsaquo; ${li.name}</option>`)
      .join("");
  },

  async renderLedgerSummary() {
    const summary = await apiGet(`/api/ledger_summary?month=${App.currentMonth}`);
    const tbody = document.querySelector("#ledger-summary-table tbody");
    tbody.innerHTML = summary.map((li) => `
      <tr>
        <td>${li.group_name}</td>
        <td>${li.name}</td>
        <td class="num">${formatCurrency(li.planned_amount)}</td>
        <td class="num">${formatCurrency(li.spent)}</td>
        <td class="num ${li.remaining < 0 ? "negative" : "positive"}">${formatCurrency(li.remaining)}</td>
      </tr>
    `).join("") || `<tr><td colspan="5" class="hint">No line items for this month yet — set up your budget first.</td></tr>`;
  },

  lineItemOptions(selectedId) {
    const unassigned = `<option value="" ${!selectedId ? "selected" : ""}>&mdash; Unassigned &mdash;</option>`;
    const rest = this.lineItemsForMonth.map((li) => `
      <option value="${li.id}" ${String(li.id) === String(selectedId) ? "selected" : ""}>${li.group_name} &rsaquo; ${li.name}</option>
    `).join("");
    return unassigned + rest;
  },

  async renderTransactions() {
    const txs = await apiGet(`/api/transactions?month=${App.currentMonth}`);
    this.lastTransactions = txs;
    const itemsById = Object.fromEntries(this.lineItemsForMonth.map((li) => [li.id, li]));

    const tbody = document.querySelector("#transactions-table tbody");
    tbody.innerHTML = txs.map((tx) => {
      const li = itemsById[tx.line_item_id];
      const checked = this.selectedTxIds.has(tx.id) ? "checked" : "";
      return `
        <tr data-tx-id="${tx.id}" data-date="${tx.date}" data-description="${(tx.description || "").replace(/"/g, "&quot;")}" data-amount="${tx.amount}" data-line-item-id="${tx.line_item_id || ""}" data-type="${tx.type || "expense"}">
          <td class="cell-date">${tx.date}</td>
          <td class="cell-line-item">${li ? `${li.group_name} &rsaquo; ${li.name}` : `<span class="hint">Unassigned</span>`}</td>
          <td class="cell-description">${tx.description || ""}</td>
          <td class="cell-type">${tx.type === "income" ? "Income" : "Expense"}</td>
          <td class="num cell-amount">${formatCurrency(tx.amount)}</td>
          <td class="tx-checkbox-col"><input type="checkbox" class="tx-select" data-id="${tx.id}" ${checked} /></td>
          <td class="row-actions">
            <button class="btn-ghost btn-tx-edit" data-id="${tx.id}">Edit</button>
            <button class="btn-ghost" data-delete-tx="${tx.id}">&times;</button>
          </td>
        </tr>
      `;
    }).join("") || `<tr><td colspan="7" class="hint">No transactions logged yet.</td></tr>`;

    tbody.querySelectorAll("[data-delete-tx]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        this.selectedTxIds.delete(parseInt(btn.dataset.deleteTx, 10));
        await apiDelete(`/api/transactions/${btn.dataset.deleteTx}`);
        await this.renderLedgerSummary();
        await this.renderTransactions();
      });
    });

    tbody.querySelectorAll(".tx-select").forEach((cb) => {
      cb.addEventListener("change", (e) => {
        const id = parseInt(e.target.dataset.id, 10);
        if (e.target.checked) this.selectedTxIds.add(id);
        else this.selectedTxIds.delete(id);
        this.updateBulkToolbar();
      });
    });

    tbody.querySelectorAll(".btn-tx-edit").forEach((btn) => {
      btn.addEventListener("click", () => this.toggleTxEdit(btn));
    });

    const selectAll = document.getElementById("tx-select-all");
    selectAll.checked = txs.length > 0 && this.selectedTxIds.size === txs.length;
    this.updateBulkToolbar();
  },

  toggleTxEdit(btn) {
    const row = btn.closest("tr");
    if (row.classList.contains("editing")) {
      this.saveTxEdit(row, btn);
      return;
    }
    row.classList.add("editing");
    btn.textContent = "Save";

    row.querySelector(".cell-date").innerHTML =
      `<input type="date" class="cell-input tx-edit-date" value="${row.dataset.date}" />`;
    row.querySelector(".cell-line-item").innerHTML =
      `<select class="cell-input tx-edit-line-item">${this.lineItemOptions(row.dataset.lineItemId)}</select>`;
    row.querySelector(".cell-description").innerHTML =
      `<input type="text" class="cell-input tx-edit-description" value="${row.dataset.description}" />`;
    // Type is editable here specifically so a transaction that was ever
    // mislabeled on import (e.g. a bank export where every Amount is
    // positive regardless of direction, so a debit got tagged "income")
    // can actually be corrected — Spent totals only ever sum type='expense'
    // transactions, so without this control a mislabeled import could never
    // be fixed no matter what Line Item it was assigned to.
    row.querySelector(".cell-type").innerHTML = `
      <select class="cell-input tx-edit-type">
        <option value="expense" ${row.dataset.type !== "income" ? "selected" : ""}>Expense</option>
        <option value="income" ${row.dataset.type === "income" ? "selected" : ""}>Income</option>
      </select>`;
    row.querySelector(".cell-amount").innerHTML =
      `<input type="number" step="0.01" class="cell-input tx-edit-amount" value="${row.dataset.amount}" />`;
  },

  async saveTxEdit(row, btn) {
    const id = row.dataset.txId;
    const date = row.querySelector(".tx-edit-date").value;
    const lineItemRaw = row.querySelector(".tx-edit-line-item").value;
    const description = row.querySelector(".tx-edit-description").value;
    const type = row.querySelector(".tx-edit-type").value;
    const amount = parseFloat(row.querySelector(".tx-edit-amount").value) || 0;

    await apiPut(`/api/transactions/${id}`, {
      date,
      description,
      type,
      amount,
      line_item_id: lineItemRaw ? parseInt(lineItemRaw, 10) : null,
    });
    await this.renderLedgerSummary();
    await this.renderTransactions();
  },

  /* ---- Bulk line-item reassignment ---- */
  updateBulkToolbar() {
    const toolbar = document.getElementById("tx-bulk-toolbar");
    const countEl = document.getElementById("tx-selected-count");
    const count = this.selectedTxIds.size;
    if (count === 0) {
      toolbar.style.display = "none";
      return;
    }
    toolbar.style.display = "flex";
    countEl.textContent = `${count} selected`;
    const select = document.getElementById("tx-bulk-line-item-select");
    select.innerHTML = this.lineItemsForMonth
      .map((li) => `<option value="${li.id}">${li.group_name} &rsaquo; ${li.name}</option>`)
      .join("") || `<option value="">No line items this month</option>`;
  },

  bindBulkToolbar() {
    const selectAll = document.getElementById("tx-select-all");
    if (!selectAll.dataset.bound) {
      selectAll.dataset.bound = "true";
      selectAll.addEventListener("change", () => {
        const ids = (this.lastTransactions || []).map((t) => t.id);
        if (selectAll.checked) ids.forEach((id) => this.selectedTxIds.add(id));
        else this.selectedTxIds.clear();
        this.renderTransactions();
      });
    }

    const applyBtn = document.getElementById("tx-bulk-apply-btn");
    if (!applyBtn.dataset.bound) {
      applyBtn.dataset.bound = "true";
      applyBtn.addEventListener("click", async () => {
        const lineItemId = document.getElementById("tx-bulk-line-item-select").value;
        if (!lineItemId) {
          alert("Choose a Line Item to assign first.");
          return;
        }
        const ids = [...this.selectedTxIds];
        if (!ids.length) return;

        await apiPut("/api/transactions/bulk_line_item", { ids, line_item_id: parseInt(lineItemId, 10) });
        this.selectedTxIds.clear();
        await this.renderLedgerSummary();
        await this.renderTransactions();
      });
    }

    const deleteBtn = document.getElementById("tx-bulk-delete-btn");
    if (!deleteBtn.dataset.bound) {
      deleteBtn.dataset.bound = "true";
      deleteBtn.addEventListener("click", async () => {
        const ids = [...this.selectedTxIds];
        if (!ids.length) return;
        if (!confirm(`Delete ${ids.length} selected transaction${ids.length === 1 ? "" : "s"}? This can't be undone.`)) return;

        const res = await fetch("/api/transactions/bulk_delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ids }),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          alert(`Couldn't delete: ${data.error || res.status}`);
          return;
        }
        this.selectedTxIds.clear();
        await this.renderLedgerSummary();
        await this.renderTransactions();
      });
    }
  },

  /* ---- CSV import ---- */
  bindImportForm() {
    const form = document.getElementById("import-form");
    if (form.dataset.bound) return;
    form.dataset.bound = "true";

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fileInput = document.getElementById("import-file-input");
      const statusEl = document.getElementById("import-status");
      const file = fileInput.files[0];
      if (!file) return;

      statusEl.textContent = "Importing...";
      const submitBtn = form.querySelector("button[type=submit]");
      submitBtn.disabled = true;

      try {
        const formData = new FormData();
        formData.append("file", file);
        const res = await fetch("/api/transactions/import", { method: "POST", body: formData });
        const result = await res.json();
        if (!res.ok) throw new Error(result.error || `Server returned ${res.status}`);

        const parts = [`Imported ${result.imported} transaction${result.imported === 1 ? "" : "s"}.`];
        if (result.skipped_duplicate) parts.push(`${result.skipped_duplicate} already imported, skipped.`);
        if (result.skipped_deposit_interest) parts.push(`${result.skipped_deposit_interest} Deposit/Interest row${result.skipped_deposit_interest === 1 ? "" : "s"} skipped (already reflected in Income).`);
        if (result.pending_credits) parts.push(`${result.pending_credits} Credit row${result.pending_credits === 1 ? "" : "s"} ${result.pending_credits === 1 ? "is" : "are"} waiting below in "Credits to Review" — add each to a Sinking Fund or dismiss it.`);
        if (result.unassigned) parts.push(`${result.unassigned} need a Line Item — select them below and use "Assign Line Item to Selected," or edit them individually.`);
        if (result.transfer_count) parts.push(`${result.transfer_count} were categorized "Transfers & Payments" (internal transfers, card bill payments) — these may not be real spending; select them and use "Delete Selected" if you'd rather not track them.`);
        if (result.errors && result.errors.length) parts.push(`${result.errors.length} row(s) had errors: ${result.errors.slice(0, 3).join(" ")}`);
        statusEl.textContent = parts.join(" ");

        // The Recent Transactions table only ever shows the current Ledger
        // Month, so a file spanning several months will silently "hide"
        // most of what was just imported behind the month picker. Surface
        // every touched month as a one-click jump instead of a sentence
        // the person has to notice and then act on manually.
        // Guarded with the null check below: if this element is ever missing
        // from index.html, the chip UI is skipped instead of throwing and
        // aborting the rest of this handler — a prior version of this code
        // threw here on a missing element, which meant form.reset() and
        // refreshLedger() below never ran, leaving the Planned vs. Spent vs.
        // Remaining table stale even though the import itself had succeeded.
        const monthNav = document.getElementById("import-month-nav");
        if (monthNav) {
          if (result.months && result.months.length > 1) {
            monthNav.innerHTML =
              `<span class="hint">This file covered ${result.months.length} months — jump to one to review it:</span> ` +
              result.months.map((m) => `<button type="button" class="btn-ghost import-month-chip" data-month="${m}">${m}</button>`).join(" ");
            monthNav.style.display = "flex";
            monthNav.querySelectorAll(".import-month-chip").forEach((btn) => {
              btn.addEventListener("click", () => setGlobalMonth(btn.dataset.month));
            });
          } else {
            monthNav.innerHTML = "";
            monthNav.style.display = "none";
          }
        }

        form.reset();
        await this.refreshLedger();
      } catch (err) {
        statusEl.textContent = `Import failed: ${err.message}`;
      } finally {
        submitBtn.disabled = false;
      }
    });
  },

  bindLedgerForm() {
    const form = document.getElementById("transaction-form");
    if (form.dataset.bound) return;
    form.dataset.bound = "true";
    form.querySelector('input[name="date"]').value = todayISO();

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      await apiPost("/api/transactions", {
        date: fd.get("date"),
        line_item_id: parseInt(fd.get("line_item_id"), 10),
        description: fd.get("description"),
        amount: parseFloat(fd.get("amount")) || 0,
        type: "expense",
      });
      form.reset();
      form.querySelector('input[name="date"]').value = todayISO();
      await this.renderLedgerSummary();
      await this.renderTransactions();
    });
  },

  /* =======================================================================
     Shared: ledger-style editable rows (Date / Amount / Edit + Delete)
     Used by both the Sinking Funds contribution tables and the Net Worth
     contribution/valuation tables, so they all look and behave like the
     Daily Ledger table above.
     ======================================================================= */
  renderEditableRow(row, type, valueField) {
    const value = row[valueField];
    return `
      <tr data-id="${row.id}" data-date="${row.date}" data-amount="${value}">
        <td class="cell-date">${row.date}</td>
        <td class="num cell-amount">${formatCurrency(value)}</td>
        <td class="row-actions">
          <button class="btn-ghost btn-row-edit" data-type="${type}" data-id="${row.id}">Edit</button>
          <button class="btn-ghost btn-row-delete" data-type="${type}" data-id="${row.id}">&times;</button>
        </td>
      </tr>
    `;
  },

  editableRowEndpoints(type, id) {
    const map = {
      "fund-contrib": {
        url: `/api/sinking_fund_contributions/${id}`,
        toBody: (date, amount) => ({ date, amount }),
        refresh: () => this.refreshFunds(),
      },
      "acct-contrib": {
        url: `/api/contributions/${id}`,
        toBody: (date, amount) => ({ date, amount }),
        refresh: () => this.refreshAccounts(),
      },
      "acct-val": {
        url: `/api/valuations/${id}`,
        toBody: (date, amount) => ({ date, value: amount }),
        refresh: () => this.refreshAccounts(),
      },
    };
    return map[type];
  },

  wireEditableRows(container) {
    container.querySelectorAll(".btn-row-edit").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const row = btn.closest("tr");
        if (row.classList.contains("editing")) {
          const type = btn.dataset.type;
          const id = btn.dataset.id;
          const date = row.querySelector(".row-edit-date").value;
          const amount = parseFloat(row.querySelector(".row-edit-amount").value) || 0;
          const cfg = this.editableRowEndpoints(type, id);
          await apiPut(cfg.url, cfg.toBody(date, amount));
          await cfg.refresh();
        } else {
          const date = row.dataset.date;
          const amount = row.dataset.amount;
          row.querySelector(".cell-date").innerHTML = `<input type="date" class="row-edit-date" value="${date}" />`;
          row.querySelector(".cell-amount").innerHTML = `<input type="number" step="0.01" class="row-edit-amount" value="${amount}" />`;
          row.classList.add("editing");
          btn.textContent = "Save";
        }
      });
    });

    container.querySelectorAll(".btn-row-delete").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const cfg = this.editableRowEndpoints(btn.dataset.type, btn.dataset.id);
        await apiDelete(cfg.url);
        await cfg.refresh();
      });
    });
  },

  /* =======================================================================
     TAB B — Sinking Funds & Goals
     ======================================================================= */
  async refreshFunds() {
    const funds = await apiGet("/api/sinking_funds");
    const wrap = document.getElementById("funds-wrap");
    wrap.innerHTML = funds.map((f) => {
      const pct = f.target_amount > 0 ? Math.min(100, (f.current_amount / f.target_amount) * 100) : 0;
      const contribRows = [...f.contributions].sort((a, b) => (a.date < b.date ? 1 : -1));

      return `
        <div class="fund-card">
          <div class="fund-card-head">
            <h3>${f.name}</h3>
            <button class="btn-ghost" data-delete-fund="${f.id}">Remove</button>
          </div>
          <div class="fund-progress-track"><div class="fund-progress-fill" style="width:0%" data-target-pct="${pct}"></div></div>
          <div class="fund-meta">
            <span>${formatCurrency(f.current_amount)} of ${formatCurrency(f.target_amount)} (${pct.toFixed(0)}%)</span>
            <span>${f.target_date ? `Target: ${f.target_date}` : ""}</span>
          </div>
          <form class="fund-add-form" data-fund-id="${f.id}">
            <input type="date" name="date" value="${todayISO()}" required />
            <input type="number" step="0.01" name="amount" placeholder="Contribution amount" required />
            <button type="submit">Add contribution</button>
          </form>

          <table class="ledger-table">
            <thead><tr><th>Date</th><th>Amount</th><th></th></tr></thead>
            <tbody>
              ${contribRows.length
                ? contribRows.map((c) => this.renderEditableRow(c, "fund-contrib", "amount")).join("")
                : `<tr><td colspan="3" class="hint">No contributions logged yet.</td></tr>`}
            </tbody>
          </table>
        </div>
      `;
    }).join("") || `<p class="hint">No sinking funds yet. Create one above.</p>`;

    animateBarFills(wrap.querySelectorAll(".fund-progress-fill"));

    wrap.querySelectorAll("[data-delete-fund]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/sinking_funds/${btn.dataset.deleteFund}`);
        await this.refreshFunds();
      });
    });

    wrap.querySelectorAll(".fund-add-form").forEach((form) => {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        await apiPost(`/api/sinking_funds/${form.dataset.fundId}/contributions`, {
          date: fd.get("date"),
          amount: parseFloat(fd.get("amount")) || 0,
        });
        await this.refreshFunds();
      });
    });

    this.wireEditableRows(wrap);

    const fundForm = document.getElementById("fund-form");
    if (!fundForm.dataset.bound) {
      fundForm.dataset.bound = "true";
      fundForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(fundForm);
        await apiPost("/api/sinking_funds", {
          name: fd.get("name"),
          target_amount: parseFloat(fd.get("target_amount")) || 0,
          target_date: fd.get("target_date") || null,
        });
        fundForm.reset();
        await this.refreshFunds();
      });
    }
  },

  /* =======================================================================
     TAB C — Net Worth Aggregator
     ======================================================================= */
  async refreshAccounts() {
    const accounts = await apiGet("/api/accounts");
    const wrap = document.getElementById("accounts-wrap");

    wrap.innerHTML = accounts.map((acc) => {
      const totalContributed = acc.contributions.reduce((s, c) => s + c.amount, 0);
      const latestValue = acc.valuations.length ? acc.valuations[acc.valuations.length - 1].value : totalContributed;
      const growth = latestValue - totalContributed;
      const contribRows = [...acc.contributions].sort((a, b) => (a.date < b.date ? 1 : -1));
      const valRows = [...acc.valuations].sort((a, b) => (a.date < b.date ? 1 : -1));

      return `
        <div class="account-card">
          <div class="account-card-head">
            <h3>${acc.name}</h3>
            <span class="account-type-tag">${acc.account_type}</span>
            <button class="btn-ghost" data-delete-account="${acc.id}">Remove</button>
          </div>
          <div class="account-figures">
            <div><div class="figure-label">Contributed</div><div class="figure-value">${formatCurrency(totalContributed)}</div></div>
            <div><div class="figure-label">Current value</div><div class="figure-value">${formatCurrency(latestValue)}</div></div>
            <div><div class="figure-label">Growth</div><div class="figure-value" style="color:${growth < 0 ? "var(--negative)" : "var(--positive)"}">${formatCurrency(growth)}</div></div>
          </div>
          <div class="account-forms">
            <form class="contribution-form" data-account-id="${acc.id}">
              <input type="date" name="date" value="${todayISO()}" required />
              <input type="number" step="0.01" name="amount" placeholder="Contribution" required />
              <button type="submit">Log contribution</button>
            </form>
            <form class="valuation-form" data-account-id="${acc.id}">
              <input type="date" name="date" value="${todayISO()}" required />
              <input type="number" step="0.01" name="value" placeholder="Current value" required />
              <button type="submit">Update value</button>
            </form>
          </div>

          <div class="account-history">
            <div>
              <h3 class="subtable-heading">Contributions</h3>
              <table class="ledger-table">
                <thead><tr><th>Date</th><th>Amount</th><th></th></tr></thead>
                <tbody>
                  ${contribRows.length
                    ? contribRows.map((c) => this.renderEditableRow(c, "acct-contrib", "amount")).join("")
                    : `<tr><td colspan="3" class="hint">No contributions logged yet.</td></tr>`}
                </tbody>
              </table>
            </div>
            <div>
              <h3 class="subtable-heading">Valuations</h3>
              <table class="ledger-table">
                <thead><tr><th>Date</th><th>Value</th><th></th></tr></thead>
                <tbody>
                  ${valRows.length
                    ? valRows.map((v) => this.renderEditableRow(v, "acct-val", "value")).join("")
                    : `<tr><td colspan="3" class="hint">No valuations logged yet.</td></tr>`}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      `;
    }).join("") || `<p class="hint">No accounts yet. Add one above.</p>`;

    wrap.querySelectorAll("[data-delete-account]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/accounts/${btn.dataset.deleteAccount}`);
        await this.refreshAccounts();
      });
    });

    wrap.querySelectorAll(".contribution-form").forEach((form) => {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        await apiPost(`/api/accounts/${form.dataset.accountId}/contributions`, {
          date: fd.get("date"),
          amount: parseFloat(fd.get("amount")) || 0,
        });
        await this.refreshAccounts();
      });
    });

    wrap.querySelectorAll(".valuation-form").forEach((form) => {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        await apiPost(`/api/accounts/${form.dataset.accountId}/valuations`, {
          date: fd.get("date"),
          value: parseFloat(fd.get("value")) || 0,
        });
        await this.refreshAccounts();
      });
    });

    this.wireEditableRows(wrap);

    const accountForm = document.getElementById("account-form");
    if (!accountForm.dataset.bound) {
      accountForm.dataset.bound = "true";
      accountForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(accountForm);
        await apiPost("/api/accounts", {
          name: fd.get("name"),
          account_type: fd.get("account_type"),
        });
        accountForm.reset();
        await this.refreshAccounts();
      });
    }

    this.lastAccounts = accounts;
    this.bindAccountsChartYearNav();
    this.renderAccountsChart(accounts);
  },

  renderAccountsChart(accounts) {
    const card = document.getElementById("networth-chart-card");
    if (!accounts.length) {
      card.style.display = "none";
      return;
    }

    if (!this.accountsChartYear) this.accountsChartYear = new Date().getFullYear();
    document.getElementById("networth-year-label").textContent = this.accountsChartYear;

    const dates = computeYearDates(accounts, this.accountsChartYear);

    if (!dates.length) {
      card.style.display = "";
      if (this.accountsChart) { this.accountsChart.destroy(); this.accountsChart = null; }
      document.getElementById("networth-accounts-chart").style.display = "none";
      let emptyMsg = document.getElementById("networth-chart-empty");
      if (!emptyMsg) {
        emptyMsg = document.createElement("p");
        emptyMsg.id = "networth-chart-empty";
        emptyMsg.className = "hint";
        document.querySelector("#networth-chart-card .chart-fixed-height").after(emptyMsg);
      }
      emptyMsg.textContent = `No account activity in ${this.accountsChartYear}.`;
      return;
    }
    card.style.display = "";
    document.getElementById("networth-accounts-chart").style.display = "";
    const emptyMsg = document.getElementById("networth-chart-empty");
    if (emptyMsg) emptyMsg.remove();

    const palette = ["#C7A15C", "#74A788", "#7A93B0", "#C3654D", "#B9A5D6", "#7FC1C6", "#D8B679", "#9FB3C8"];

    const datasets = accounts.map((acc, i) => ({
      label: acc.name,
      data: dates.map((d) => accountValueAt(acc, d)),
      borderColor: palette[i % palette.length],
      backgroundColor: "transparent",
      tension: 0.2,
    }));

    const ctx = document.getElementById("networth-accounts-chart");
    if (this.accountsChart) this.accountsChart.destroy();
    this.accountsChart = new Chart(ctx, {
      type: "line",
      data: { labels: dates, datasets },
      options: {
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: "#93A0AF" }, grid: { color: "#28323F" } },
          y: { ticks: { color: "#93A0AF" }, grid: { color: "#28323F" } },
        },
        plugins: { legend: { labels: { color: "#E9E4D8", font: { family: "Inter" } } } },
      },
    });
  },

  bindAccountsChartYearNav() {
    if (this._accountsChartYearNavBound) return;
    this._accountsChartYearNavBound = true;

    document.getElementById("networth-year-prev").addEventListener("click", () => {
      this.accountsChartYear = (this.accountsChartYear || new Date().getFullYear()) - 1;
      this.renderAccountsChart(this.lastAccounts || []);
    });
    document.getElementById("networth-year-next").addEventListener("click", () => {
      this.accountsChartYear = (this.accountsChartYear || new Date().getFullYear()) + 1;
      this.renderAccountsChart(this.lastAccounts || []);
    });
  },
};