/* =========================================================================
   track.js - Page 2: Track (three sub-tabs)
   ========================================================================= */

const Track = {
  ledgerSummaryView: "detailed",
  lineItemsForMonth: [],
  accountsChart: null,
  accountsChartYear: null,
  lastAccounts: [],
  selectedTxIds: new Set(),
  expandedAccountHistoryIds: new Set(),
  expandedDebtHistoryIds: new Set(),
  expandedFundHistoryIds: new Set(),
  fundsOverviewChart: null,
  excludedFundIds: new Set(),
  lastPayoffPlan: null,
  lastDebts: [],

  async refresh() {
    if (document.getElementById("page-track").classList.contains("active") === false) return;
    await this.refreshLedger();
  },

  /* =======================================================================
     TAB A - Daily Ledger
     ======================================================================= */
  async refreshLedger() {
    this.lineItemsForMonth = await apiGet(`/api/budget/line_items?month=${App.currentMonth}`);
    this.selectedTxIds.clear();
    this.populateLineItemSelect();
    this.populateAutomateLineItemSelect();
    await this.populateFundSelect();
    await this.renderLedgerSummary();
    await this.renderTransactions();
    await this.renderPendingCredits();
    await this.renderAutomateRules();
    this.bindLedgerForm();
    this.bindImportForm();
    this.bindBulkToolbar();
    this.bindTxSearchForm();
    this.bindTxActionToggle();
    this.bindLedgerSummaryToggle(); // <-- Add this
    this.bindAutomateForm();
    this.bindSplitModal();
    this.bindClearForm();
    this.bindAutomateApplyMonthButton();
    autoExpandIfEmpty("tx-actions-card", (this.lastTransactions || []).length === 0);
  },

  /**
   * Log / Import / Search / Automate toggle bar for the combined
   * Transactions card - purely a visual/navigation switch between the four
   * panels; each panel's form and its event bindings are completely
   * unchanged.
   */
  bindTxActionToggle() {
    const bar = document.getElementById("tx-actions-toggle");
    if (bar.dataset.bound) return;
    bar.dataset.bound = "true";
    bar.querySelectorAll(".tab-toggle-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        bar.querySelectorAll(".tab-toggle-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        document.querySelectorAll(".tx-action-panel").forEach((p) => p.classList.remove("active"));
        document.getElementById(`tx-action-${btn.dataset.txAction}`).classList.add("active");
      });
    });
  },

  /* ---- Automate: "if description contains X, categorize as Group > Item" rules ---- */
  async renderAutomateRules() {
    const rules = await apiGet("/api/automate/rules");
    const table = document.getElementById("automate-rules-table");
    const empty = document.getElementById("automate-rules-empty");
    const tbody = table.querySelector("tbody");

    if (!rules.length) {
      table.style.display = "none";
      empty.style.display = "";
      return;
    }
    table.style.display = "";
    empty.style.display = "none";

    tbody.innerHTML = rules.map((r) => `
      <tr data-rule-id="${r.id}">
        <td><input type="text" class="automate-rule-pattern-input" value="${r.pattern.replace(/"/g, "&quot;")}" /></td>
        <td><select class="automate-rule-item-select">${this.lineItemOptionsHtml(r.group_name, r.item_name)}</select></td>
        <td><button type="button" class="btn-ghost" data-delete-rule="${r.id}">Remove</button></td>
      </tr>
    `).join("");

    tbody.querySelectorAll(".automate-rule-pattern-input").forEach((input) => {
      const original = input.value;
      input.addEventListener("change", async () => {
        const ruleId = input.closest("tr").dataset.ruleId;
        if (!input.value.trim()) {
          showToast("Pattern can't be empty.", "warning");
          input.value = original;
          return;
        }
        await apiPut(`/api/automate/rules/${ruleId}`, { pattern: input.value.trim() });
      });
    });

    tbody.querySelectorAll(".automate-rule-item-select").forEach((select) => {
      select.addEventListener("change", async () => {
        const ruleId = select.closest("tr").dataset.ruleId;
        const opt = select.selectedOptions[0];
        await apiPut(`/api/automate/rules/${ruleId}`, { group_name: opt.dataset.group, item_name: opt.dataset.item });
      });
    });

    tbody.querySelectorAll("[data-delete-rule]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Remove this Automate rule? Transactions already categorized by it won't be un-categorized.")) return;
        await apiDelete(`/api/automate/rules/${btn.dataset.deleteRule}`);
        await this.renderAutomateRules();
      });
    });
  },

  bindAutomateForm() {
    const form = document.getElementById("automate-rule-form");
    if (form.dataset.bound) return;
    form.dataset.bound = "true";
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const select = document.getElementById("automate-rule-line-item-select");
      const opt = select.selectedOptions[0];
      if (!opt || !opt.dataset.group) {
        showToast("Pick a Line Item for this rule.", "warning");
        return;
      }
      try {
        const result = await apiPost("/api/automate/rules", {
          pattern: fd.get("pattern"),
          group_name: opt.dataset.group,
          item_name: opt.dataset.item,
          apply_to_existing: fd.get("apply_to_existing") === "on",
          only_unassigned: true,
        });
        form.reset();
        await this.renderAutomateRules();
        if (result.applied) {
          await this.renderTransactions();
          await this.renderLedgerSummary();
        }
        if (typeof result.applied === "number" && result.applied > 0) {
          showToast(`Rule saved and applied to ${result.applied} existing unassigned transaction(s).`, "success");
        }
      } catch (err) {
        showToast(`Couldn't save that rule: ${err.message}`, "error");
      }
    });
  },

  /* ---- Automate: run every existing rule against the current Ledger
     Month in one pass - catches rows imported before a rule existed, or
     before it was edited, without re-triggering a full CSV re-import. ---- */
  bindAutomateApplyMonthButton() {
    const btn = document.getElementById("automate-apply-month-btn");
    if (btn.dataset.bound) return;
    btn.dataset.bound = "true";

    btn.addEventListener("click", async () => {
      const month = App.currentMonth;
      const status = document.getElementById("automate-apply-month-status");
      const rules = await apiGet("/api/automate/rules");
      if (!rules.length) {
        showToast("No Automate rules to apply yet - add one above.", "warning");
        return;
      }
      if (!confirm(`Apply all ${rules.length} Automate rule${rules.length === 1 ? "" : "s"} to every unassigned transaction in ${month}?`)) return;

      btn.disabled = true;
      status.textContent = "Applying...";
      try {
        const result = await apiPost("/api/automate/rules/apply_to_month", { month, only_unassigned: true });
        status.textContent = "";
        showToast(`Categorized ${result.updated} transaction${result.updated === 1 ? "" : "s"} in ${month}.`, result.updated ? "success" : "info");
        if (result.updated) {
          await this.renderTransactions();
          await this.renderLedgerSummary();
        }
      } catch (err) {
        showToast(`Couldn't apply rules: ${err.message}`, "error");
      } finally {
        btn.disabled = false;
      }
    });
  },

  /* ---- Clear: bulk-remove transactions by date range, or all time ---- */
  bindClearForm() {
    const form = document.getElementById("tx-clear-form");
    if (form.dataset.bound) return;
    form.dataset.bound = "true";

    const allTimeCheckbox = document.getElementById("tx-clear-all-time");
    const fromInput = document.getElementById("tx-clear-date-from");
    const toInput = document.getElementById("tx-clear-date-to");
    const status = document.getElementById("tx-clear-status");

    allTimeCheckbox.addEventListener("change", () => {
      fromInput.disabled = allTimeCheckbox.checked;
      toInput.disabled = allTimeCheckbox.checked;
      if (allTimeCheckbox.checked) {
        fromInput.value = "";
        toInput.value = "";
      }
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const allTime = allTimeCheckbox.checked;
      const dateFrom = allTime ? null : (fromInput.value || null);
      const dateTo = allTime ? null : (toInput.value || null);

      if (!allTime && !dateFrom && !dateTo) {
        showToast('Pick a start and/or stop date, or check "All time".', "warning");
        return;
      }
      if (!allTime && dateFrom && dateTo && dateFrom > dateTo) {
        showToast("Start date must be on or before the stop date.", "warning");
        return;
      }

      const params = new URLSearchParams();
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      const { count } = await apiGet(`/api/transactions/clear/count?${params.toString()}`);

      if (count === 0) {
        status.textContent = "No transactions match that range.";
        return;
      }

      const rangeLabel = !dateFrom && !dateTo
        ? "all time"
        : `${dateFrom || "the beginning"} through ${dateTo || "today"}`;
      const confirmMsg = allTime
        ? `Permanently delete ALL ${count} transaction${count === 1 ? "" : "s"}, for all time? This can't be undone.`
        : `Delete ${count} transaction${count === 1 ? "" : "s"} from ${rangeLabel}? This can't be undone.`;
      if (!confirm(confirmMsg)) return;

      try {
        const result = await apiPost("/api/transactions/clear", { date_from: dateFrom, date_to: dateTo });
        showToast(`Cleared ${result.deleted} transaction${result.deleted === 1 ? "" : "s"}.`, "success");
        status.textContent = "";
        form.reset();
        fromInput.disabled = false;
        toInput.disabled = false;
        await this.renderTransactions();
        await this.renderLedgerSummary();
      } catch (err) {
        showToast(`Couldn't clear transactions: ${err.message}`, "error");
      }
    });
  },

  /* ---- Pending Credits (Credit-type import rows awaiting fund routing) ---- */
  async renderPendingCredits() {
    const credits = await apiGet("/api/pending_credits");
    const card = document.getElementById("pending-credits-card");
    const wrap = document.getElementById("pending-credits-wrap");

    // Not month-scoped like the rest of the Daily Ledger tab - a Credit
    // row sits here until it's actioned, regardless of which Ledger Month
    // happens to be selected, so nothing gets lost behind the month picker.
    if (!credits.length) {
      card.style.display = "none";
      return;
    }
    card.style.display = "";

    const funds = await apiGet("/api/sinking_funds");

    // Budget categories are month-scoped, but pending credits can span many
    // months (an import can cover a CSV's whole date range) - fetch each
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
        if (!dest) { showToast("Choose a Fund or Budget Category first.", "warning"); return; }
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
          showToast(`Couldn't add that: ${err.message}`, "error");
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

  /**
   * Builds "Group > Item" <option>s from the current month's line items.
   * If `currentGroup`/`currentItem` don't match anything in that list (the
   * rule was created against a different month's categories), a disabled
   * placeholder option is prepended so editing the row doesn't silently
   * reassign it to whatever the first real option happens to be.
   */
  lineItemOptionsHtml(currentGroup, currentItem) {
    const match = this.lineItemsForMonth.some((li) => li.group_name === currentGroup && li.name === currentItem);
    const placeholder = (!match && currentGroup)
      ? `<option value="" disabled selected data-group="${currentGroup}" data-item="${currentItem}">${currentGroup} &rsaquo; ${currentItem} (not in this month)</option>`
      : "";
    const options = this.lineItemsForMonth
      .map((li) => `<option value="${li.id}" data-group="${li.group_name}" data-item="${li.name}" ${(!placeholder && li.group_name === currentGroup && li.name === currentItem) ? "selected" : ""}>${li.group_name} &rsaquo; ${li.name}</option>`)
      .join("");
    return placeholder + options;
  },

  populateAutomateLineItemSelect() {
    document.getElementById("automate-rule-line-item-select").innerHTML = this.lineItemOptionsHtml();
  },

  async populateFundSelect() {
    const funds = await apiGet("/api/sinking_funds");
    this.fundsForLedger = funds;
    const select = document.getElementById("tx-fund-select");
    select.innerHTML = funds.length
      ? funds.map((f) => `<option value="${f.id}">${f.name}</option>`).join("")
      : `<option value="">No sinking funds yet</option>`;
  },

  async renderLedgerSummary() {
    const summary = await apiGet(`/api/ledger_summary?month=${App.currentMonth}`);

    let displayData = summary;
    const table = document.getElementById("ledger-summary-table");

    if (this.ledgerSummaryView === "summary") {
      table.classList.add("summary-view");
      const groups = {};
      summary.forEach(li => {
        if (!groups[li.group_name]) {
          groups[li.group_name] = {
            group_name: li.group_name,
            name: "",
            planned_amount: 0,
            spent: 0,
            remaining: 0
          };
        }
        groups[li.group_name].planned_amount += li.planned_amount;
        groups[li.group_name].spent += li.spent;
        groups[li.group_name].remaining += li.remaining;
      });
      displayData = Object.values(groups);
    } else {
      table.classList.remove("summary-view");
    }

    const tbody = document.querySelector("#ledger-summary-table tbody");
    tbody.innerHTML = displayData.map((li) => {
      const hasPlan = li.planned_amount > 0;
      const pct = hasPlan ? (li.spent / li.planned_amount) * 100 : 0;
      const barColor = pct > 100 ? "var(--negative)" : pct >= 90 ? "var(--warning)" : "var(--positive)";
      const progressCell = hasPlan
        ? `<div class="progress-bar-track compact"><div class="progress-bar-fill" style="width:0%; background:${barColor}" data-target-pct="${Math.min(100, pct)}"></div></div>`
        : `<span class="hint">N/A</span>`;
      return `
        <tr>
          <td>${li.group_name}</td>
          <td class="line-item-col">${li.name}</td>
          <td class="progress-col">${progressCell}</td>
          <td class="num">${formatCurrency(li.planned_amount)}</td>
          <td class="num">${formatCurrency(li.spent)}</td>
          <td class="num ${li.remaining < 0 ? "negative" : "positive"}">${formatCurrency(li.remaining)}</td>
        </tr>
      `;
    }).join("") || `<tr><td colspan="${this.ledgerSummaryView === 'summary' ? 5 : 6}" class="hint">No line items for this month yet - set up your budget first.</td></tr>`;

    animateBarFills(tbody.querySelectorAll(".progress-bar-fill"));

    const tfoot = document.querySelector("#ledger-summary-table tfoot");
    if (!summary.length) {
      tfoot.innerHTML = "";
      return;
    }
    const totalPlanned = summary.reduce((sum, li) => sum + li.planned_amount, 0);
    const totalSpent = summary.reduce((sum, li) => sum + li.spent, 0);
    const totalRemaining = totalPlanned - totalSpent;
    const colSpan = this.ledgerSummaryView === "summary" ? 2 : 3;

    tfoot.innerHTML = `
      <tr>
        <td colspan="${colSpan}">Total</td>
        <td class="num">${formatCurrency(totalPlanned)}</td>
        <td class="num">${formatCurrency(totalSpent)}</td>
        <td class="num ${totalRemaining < 0 ? "negative" : "positive"}">${formatCurrency(totalRemaining)}</td>
      </tr>
    `;
  },

  /**
   * Cross-month transaction search - the rest of the Daily Ledger tab is
   * scoped to whichever Ledger Month is currently selected; this hits
   * every transaction regardless of month.
   */
  bindTxSearchForm() {
    const select = document.getElementById("tx-search-line-item");
    select.innerHTML = `<option value="">Any line item</option>` +
      this.lineItemsForMonth.map((li) => `<option value="${li.id}">${li.group_name} &rsaquo; ${li.name}</option>`).join("");

    const form = document.getElementById("tx-search-form");
    if (form.dataset.bound) return;
    form.dataset.bound = "true";

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const params = new URLSearchParams();
      ["q", "date_from", "date_to", "line_item_id", "type", "min_amount", "max_amount"].forEach((key) => {
        const val = fd.get(key);
        if (val) params.set(key, val);
      });
      const results = await apiGet(`/api/transactions/search?${params.toString()}`);
      this.renderTxSearchResults(results);
    });
  },

  renderTxSearchResults(results) {
    const table = document.getElementById("tx-search-results-table");
    const tbody = table.querySelector("tbody");
    const status = document.getElementById("tx-search-status");

    if (!results.length) {
      table.style.display = "none";
      status.textContent = "No matching transactions found.";
      return;
    }

    table.style.display = "";
    status.textContent = `${results.length} matching transaction${results.length === 1 ? "" : "s"}.`;
    tbody.innerHTML = results.map((tx) => `
      <tr>
        <td>${tx.date}</td>
        <td>${tx.line_item_name ? `${tx.group_name} &rsaquo; ${tx.line_item_name}` : "Unassigned"}</td>
        <td>${tx.description || ""}</td>
        <td>${tx.type}</td>
        <td class="num">${formatCurrency(tx.amount)}</td>
      </tr>
    `).join("");
  },

  lineItemOptions(selectedId) {
    const unassigned = `<option value="" ${!selectedId ? "selected" : ""}>Unassigned</option>`;
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
      const splitBadge = tx.split_group_id
        ? `<span class="split-badge" title="Part of a split transaction">Split</span>`
        : "";
      // Unassigned rows with a real merchant description get a one-click
      // shortcut into the Automate tab, pattern pre-filled, instead of
      // requiring a trip there to retype it by hand.
      const automateShortcut = (!li && tx.description)
        ? `<button type="button" class="tx-automate-shortcut" data-automate-shortcut="${tx.id}" data-pattern="${tx.description.replace(/"/g, "&quot;")}">+ Automate rule</button>`
        : "";
      return `
        <tr data-tx-id="${tx.id}" data-date="${tx.date}" data-description="${(tx.description || "").replace(/"/g, "&quot;")}" data-amount="${tx.amount}" data-line-item-id="${tx.line_item_id || ""}" data-type="${tx.type || "expense"}" data-split-group-id="${tx.split_group_id || ""}">
          <td class="cell-date">${tx.date}</td>
          <td class="cell-line-item">${li ? `${li.group_name} &rsaquo; ${li.name}` : `<span class="hint">Unassigned</span>`}</td>
          <td class="cell-description">${tx.description || ""}${splitBadge}${automateShortcut}</td>
          <td class="cell-type">${tx.type === "income" ? "Income" : "Expense"}</td>
          <td class="num cell-amount">${formatCurrency(tx.amount)}</td>
          <td class="tx-checkbox-col"><input type="checkbox" class="tx-select" data-id="${tx.id}" ${checked} /></td>
          <td class="row-actions">
            <div class="row-actions-menu">
              <button type="button" class="row-actions-menu-btn" data-menu-toggle aria-haspopup="true" aria-expanded="false" title="Actions">&#8942;</button>
              <div class="row-actions-menu-list">
                <button type="button" class="btn-tx-edit" data-id="${tx.id}">Edit</button>
                <button type="button" class="btn-tx-split" data-id="${tx.id}">Split</button>
                ${tx.split_group_id ? `<button type="button" class="btn-tx-unsplit" data-split-group-id="${tx.split_group_id}">Un-split</button>` : ""}
                <button type="button" class="danger" data-delete-tx="${tx.id}">Delete</button>
              </div>
            </div>
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

    tbody.querySelectorAll(".btn-tx-split").forEach((btn) => {
      btn.addEventListener("click", () => {
        const tx = this.lastTransactions.find((t) => String(t.id) === btn.dataset.id);
        if (tx) this.openSplitModal(tx);
      });
    });

    tbody.querySelectorAll(".btn-tx-unsplit").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Merge this split back into one transaction? The individual part assignments will be lost.")) return;
        await apiPost(`/api/transactions/split_groups/${btn.dataset.splitGroupId}/unsplit`, {});
        await this.renderLedgerSummary();
        await this.renderTransactions();
      });
    });

    tbody.querySelectorAll("[data-automate-shortcut]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        this.activateAutomateShortcut(btn.dataset.pattern);
      });
    });

    this.bindRowActionsMenus(tbody);

    const selectAll = document.getElementById("tx-select-all");
    selectAll.checked = txs.length > 0 && this.selectedTxIds.size === txs.length;
    this.updateBulkToolbar();
  },

  /**
   * Wires up the per-row "..." action menu: click to open/close, click
   * anywhere else to close, and closes itself the moment any action inside
   * it is clicked (so Edit/Split/Un-split/Delete don't leave a stray open
   * popover behind). Re-bound on every render since the rows themselves
   * are rebuilt each time, but the single document-level "click outside"
   * listener is only ever attached once.
   */
  bindRowActionsMenus(tbody) {
    tbody.querySelectorAll("[data-menu-toggle]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const menu = btn.closest(".row-actions-menu");
        const wasOpen = menu.classList.contains("open");
        document.querySelectorAll(".row-actions-menu.open").forEach((m) => {
          m.classList.remove("open");
          m.querySelector("[data-menu-toggle]").setAttribute("aria-expanded", "false");
        });
        if (!wasOpen) {
          menu.classList.add("open");
          btn.setAttribute("aria-expanded", "true");
        }
      });
    });

    tbody.querySelectorAll(".row-actions-menu-list").forEach((list) => {
      list.addEventListener("click", (e) => {
        if (e.target.tagName === "BUTTON") list.closest(".row-actions-menu").classList.remove("open");
      });
    });

    if (!this._rowMenuDocBound) {
      this._rowMenuDocBound = true;
      document.addEventListener("click", () => {
        document.querySelectorAll(".row-actions-menu.open").forEach((m) => {
          m.classList.remove("open");
          m.querySelector("[data-menu-toggle]").setAttribute("aria-expanded", "false");
        });
      });
    }
  },

  /**
   * Jumps straight to the Automate tab within the Transactions card,
   * expanding the card if it's collapsed and pre-filling the pattern field
   * with this transaction's description - so categorizing a recurring
   * unassigned merchant doesn't require retyping its name by hand.
   */
  activateAutomateShortcut(patternText) {
    const card = document.getElementById("tx-actions-card");
    const toggleBtn = card.querySelector("[data-card-toggle]");
    if (card.classList.contains("collapsed")) {
      setCardCollapsed(card, toggleBtn, false);
      apiPut(`/api/settings/card_collapsed_${card.id}`, { value: "false" }).catch(() => {});
    }

    const automateTabBtn = document.querySelector('#tx-actions-toggle [data-tx-action="automate"]');
    if (automateTabBtn && !automateTabBtn.classList.contains("active")) automateTabBtn.click();

    const patternInput = document.querySelector('#automate-rule-form input[name="pattern"]');
    if (patternInput) patternInput.value = patternText;

    card.scrollIntoView({ behavior: "smooth", block: "start" });
    const lineItemSelect = document.getElementById("automate-rule-line-item-select");
    if (lineItemSelect) lineItemSelect.focus();
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
    // can actually be corrected - Spent totals only ever sum type='expense'
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

  /* ---- Split Transaction modal ---- */
  openSplitModal(tx) {
    this.splitTxOriginal = tx;
    document.getElementById("split-tx-original-info").textContent =
      `${tx.date} \u00b7 ${tx.description || "(no description)"} \u00b7 Total: ${formatCurrency(tx.amount)}`;
    document.getElementById("split-tx-rows").innerHTML = "";
    document.getElementById("split-tx-status").textContent = "";
    this.addSplitRow(tx.amount / 2, tx.description);
    this.addSplitRow(tx.amount / 2, tx.description);
    this.updateSplitRemaining();
    ModalManager.open("split-tx-modal");
  },

  addSplitRow(amount, description) {
    const row = document.createElement("div");
    row.className = "split-tx-row";
    row.innerHTML = `
      <select class="split-line-item">${this.lineItemOptions("")}</select>
      <input type="text" class="split-description" placeholder="Description (optional)" value="${(description || "").replace(/"/g, "&quot;")}" />
      <input type="number" step="0.01" class="split-amount" value="${amount.toFixed(2)}" />
      <button type="button" class="btn-ghost split-remove-row">&times;</button>
    `;
    document.getElementById("split-tx-rows").appendChild(row);
    row.querySelector(".split-amount").addEventListener("input", () => this.updateSplitRemaining());
    row.querySelector(".split-remove-row").addEventListener("click", () => {
      row.remove();
      this.updateSplitRemaining();
    });
  },

  updateSplitRemaining() {
    const rows = document.querySelectorAll("#split-tx-rows .split-tx-row");
    const total = Array.from(rows).reduce(
      (sum, r) => sum + (parseFloat(r.querySelector(".split-amount").value) || 0), 0
    );
    const remaining = this.splitTxOriginal.amount - total;
    const el = document.getElementById("split-tx-remaining");
    if (Math.abs(remaining) < 0.01) {
      el.textContent = "Balanced - these amounts add up to the original total.";
      el.className = "split-tx-remaining balanced";
    } else if (remaining > 0) {
      el.textContent = `${formatCurrency(remaining)} left to assign.`;
      el.className = "split-tx-remaining unbalanced";
    } else {
      el.textContent = `${formatCurrency(-remaining)} over the original total.`;
      el.className = "split-tx-remaining unbalanced";
    }
  },

  bindSplitModal() {
    if (this._splitModalBound) return;
    this._splitModalBound = true;

    const statusEl = document.getElementById("split-tx-status");
    const confirmBtn = document.getElementById("split-tx-confirm");

    ModalManager.register("split-tx-modal");
    document.getElementById("split-tx-cancel").addEventListener("click", () => ModalManager.close("split-tx-modal"));
    document.getElementById("split-tx-add-row-btn").addEventListener("click", () => this.addSplitRow(0, this.splitTxOriginal.description));

    confirmBtn.addEventListener("click", async () => {
      const rows = document.querySelectorAll("#split-tx-rows .split-tx-row");
      const splits = Array.from(rows).map((r) => ({
        line_item_id: r.querySelector(".split-line-item").value ? parseInt(r.querySelector(".split-line-item").value, 10) : null,
        description: r.querySelector(".split-description").value,
        amount: parseFloat(r.querySelector(".split-amount").value) || 0,
      }));

      confirmBtn.disabled = true;
      statusEl.textContent = "Splitting...";
      try {
        const res = await fetch(`/api/transactions/${this.splitTxOriginal.id}/split`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ splits }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `Server returned ${res.status}`);
        ModalManager.close("split-tx-modal");
        await this.renderLedgerSummary();
        await this.renderTransactions();
      } catch (err) {
        statusEl.textContent = err.message;
      } finally {
        confirmBtn.disabled = false;
      }
    });
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
          showToast("Choose a Line Item to assign first.", "warning");
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
          showToast(`Couldn't delete: ${data.error || res.status}`, "error");
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
        if (result.pending_credits) parts.push(`${result.pending_credits} Credit row${result.pending_credits === 1 ? "" : "s"} ${result.pending_credits === 1 ? "is" : "are"} waiting below in "Credits to Review" - add each to a Sinking Fund or dismiss it.`);
        if (result.unassigned) parts.push(`${result.unassigned} need a Line Item - select them below and use "Assign Line Item to Selected," or edit them individually.`);
        if (result.transfer_count) parts.push(`${result.transfer_count} were categorized "Transfers & Payments" (internal transfers, card bill payments) - these may not be real spending; select them and use "Delete Selected" if you'd rather not track them.`);
        if (result.errors && result.errors.length) parts.push(`${result.errors.length} row(s) had errors: ${result.errors.slice(0, 3).join(" ")}`);
        statusEl.textContent = parts.join(" ");

        // The Recent Transactions table only ever shows the current Ledger
        // Month, so a file spanning several months will silently "hide"
        // most of what was just imported behind the month picker. Surface
        // every touched month as a one-click jump instead of a sentence
        // the person has to notice and then act on manually.
        // Guarded with the null check below: if this element is ever missing
        // from index.html, the chip UI is skipped instead of throwing and
        // aborting the rest of this handler - a prior version of this code
        // threw here on a missing element, which meant form.reset() and
        // refreshLedger() below never ran, leaving the Planned vs. Spent vs.
        // Remaining table stale even though the import itself had succeeded.
        const monthNav = document.getElementById("import-month-nav");
        if (monthNav) {
          if (result.months && result.months.length > 1) {
            monthNav.innerHTML =
              `<span class="hint">This file covered ${result.months.length} months - jump to one to review it:</span> ` +
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

    const typeSelect = document.getElementById("tx-log-type");
    const lineItemSelect = document.getElementById("line-item-select");
    const fundSelect = document.getElementById("tx-fund-select");

    // A logged Credit routes straight to a Sinking Fund contribution (same
    // destination as a pending-credit's "assign to fund" action) rather than
    // becoming a transaction row, so the Line Item picker isn't relevant -
    // swap it out for the Fund picker instead.
    typeSelect.addEventListener("change", () => {
      const isCredit = typeSelect.value === "credit";
      lineItemSelect.style.display = isCredit ? "none" : "";
      lineItemSelect.required = !isCredit;
      fundSelect.style.display = isCredit ? "" : "none";
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const type = fd.get("type");

      if (type === "credit") {
        const fundId = fd.get("fund_id");
        if (!fundId) { showToast("Choose a Sinking Fund to credit first.", "warning"); return; }
        await apiPost(`/api/sinking_funds/${fundId}/contributions`, {
          date: fd.get("date"),
          amount: parseFloat(fd.get("amount")) || 0,
        });
        await this.refreshFunds();
      } else {
        await apiPost("/api/transactions", {
          date: fd.get("date"),
          line_item_id: parseInt(fd.get("line_item_id"), 10),
          description: fd.get("description"),
          amount: parseFloat(fd.get("amount")) || 0,
          type: "expense",
        });
        await this.renderLedgerSummary();
        await this.renderTransactions();
      }

      form.reset();
      form.querySelector('input[name="date"]').value = todayISO();
      typeSelect.value = "expense";
      typeSelect.dispatchEvent(new Event("change"));
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

  bindLedgerSummaryToggle() {
    const bar = document.getElementById("ledger-summary-toggle");
    if (!bar || bar.dataset.bound) return;
    bar.dataset.bound = "true";
    bar.querySelectorAll(".tab-toggle-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        bar.querySelectorAll(".tab-toggle-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        this.ledgerSummaryView = btn.dataset.view;
        this.renderLedgerSummary();
      });
    });
  },

  /* =======================================================================
     TAB B - Sinking Funds & Goals
     ======================================================================= */
  /**
   * One stacked horizontal bar per fund/goal - Saved vs. Remaining (vs.
   * Over target, for a fund that's exceeded its goal) - so progress across
   * every fund is visible at a glance without opening each card.
   */
  /**
   * One stacked horizontal bar per fund/goal - Saved vs. Remaining (vs.
   * Over target, for a fund that's exceeded its goal) - so progress across
   * every fund is visible at a glance without opening each card. Clicking
   * a fund's name in the Y-axis label gutter hides it from the chart
   * (kept in excludedFundIds so it stays hidden across refreshes, e.g.
   * after logging a new contribution) - useful when one outsized goal
   * (a house down payment, say) makes every smaller fund's bar look tiny
   * by comparison. A "Show all funds" link appears whenever anything's
   * hidden, to undo it.
   */
  renderFundsOverviewChart(allFunds) {
    const canvas = document.getElementById("funds-overview-chart");
    const card = document.getElementById("funds-overview-card");
    const resetBtn = document.getElementById("funds-overview-reset-btn");

    if (!resetBtn.dataset.bound) {
      resetBtn.dataset.bound = "true";
      resetBtn.addEventListener("click", () => {
        this.excludedFundIds.clear();
        this.renderFundsOverviewChart(this.lastFundsForChart || []);
      });
    }
    this.lastFundsForChart = allFunds;

    if (!allFunds.length) {
      if (this.fundsOverviewChart) { this.fundsOverviewChart.destroy(); this.fundsOverviewChart = null; }
      card.style.display = "none";
      return;
    }
    card.style.display = "";
    resetBtn.style.display = this.excludedFundIds.size ? "" : "none";

    const funds = allFunds.filter((f) => !this.excludedFundIds.has(f.id));
    if (!funds.length) {
      if (this.fundsOverviewChart) { this.fundsOverviewChart.destroy(); this.fundsOverviewChart = null; }
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }

    const labels = funds.map((f) => f.name);
    const saved = funds.map((f) => Math.min(f.current_amount, f.target_amount || f.current_amount));
    const remaining = funds.map((f) => Math.max((f.target_amount || 0) - f.current_amount, 0));
    const overTarget = funds.map((f) => Math.max(f.current_amount - (f.target_amount || 0), 0));
    const hasOverTarget = overTarget.some((v) => v > 0);

    const datasets = [
      { label: "Saved", data: saved, backgroundColor: "#C7A15C", stack: "s" },
      { label: "Remaining", data: remaining, backgroundColor: "#3A4552", stack: "s" },
    ];
    if (hasOverTarget) datasets.push({ label: "Over target", data: overTarget, backgroundColor: "#74A788", stack: "s" });

    if (this.fundsOverviewChart) this.fundsOverviewChart.destroy();
    this.fundsOverviewChart = new Chart(canvas, {
      type: "bar",
      data: { labels, datasets },
      options: {
        indexAxis: "y",
        scales: {
          x: { stacked: true, ticks: { color: "#93A0AF", callback: (v) => formatCurrency(v) }, grid: { color: "#28323F" } },
          y: { stacked: true, ticks: { color: "#93A0AF" }, grid: { display: false } },
        },
        plugins: {
          legend: { labels: { color: "#E9E4D8", font: { family: "Inter" } } },
          tooltip: { callbacks: { label: (item) => `${item.dataset.label}: ${formatCurrency(item.raw)}` } },
        },
        // NOTE: onHover/onClick are intentionally NOT set here. On this
        // canvas, Chart.js's own click dispatch was confirmed (via live
        // debugging) to never invoke options.onClick on a real mouse
        // click, even though a plain addEventListener("click", ...) on
        // the exact same canvas fires every time with correct
        // coordinates, and manually invoking a would-be onClick callback
        // worked fine. Rather than depend on Chart.js's internal event
        // routing (whatever is swallowing it there), the label-gutter
        // click/hover handling below is wired directly to the canvas
        // element instead, reusing Chart.js's own coordinate-conversion
        // helper (getRelativePosition) so the hit-testing math is
        // unchanged - only the event source moved.
      },
    });

    if (!canvas.dataset.fundsClickBound) {
      canvas.dataset.fundsClickBound = "true";

      canvas.addEventListener("mousemove", (e) => {
        const chart = this.fundsOverviewChart;
        if (!chart) return;
        const pos = Chart.helpers.getRelativePosition(e, chart);
        const inLabelGutter = this.fundLabelIndexAtEvent(chart, pos) !== null;
        canvas.style.cursor = inLabelGutter ? "pointer" : "default";
      });

      canvas.addEventListener("click", (e) => {
        const chart = this.fundsOverviewChart;
        if (!chart) return;
        const pos = Chart.helpers.getRelativePosition(e, chart);
        const index = this.fundLabelIndexAtEvent(chart, pos);
        if (index === null) return;
        const currentFunds = (this.lastFundsForChart || []).filter((f) => !this.excludedFundIds.has(f.id));
        if (index >= currentFunds.length) return;
        this.excludedFundIds.add(currentFunds[index].id);
        this.renderFundsOverviewChart(this.lastFundsForChart);
      });
    }
  },

  /**
   * Returns the fund index whose Y-axis label the event is over, or null.
   * Originally bounded against the y-scale's own reported box
   * (yScale.left/right/top/bottom), but that box isn't reliable for this
   * purpose in Chart.js 4.x - a category scale's left/right sometimes
   * collapses to a sliver flush with the plot area rather than the full
   * label-text gutter, so clicks on the actual label text landed just
   * outside the bounds check and silently did nothing.
   *
   * Bounding against chart.chartArea instead is reliable: chartArea is
   * always the exact rectangle the bars are plotted in, computed the same
   * way regardless of scale/label quirks. Since the y-axis sits to the
   * left of the plot with nothing else out there, any click left of
   * chartArea.left (and within its vertical range) is unambiguously a
   * click in the label gutter - no need to trust the scale's own box.
   */
  fundLabelIndexAtEvent(chart, evt) {
    if (evt.x === null || evt.y === null) return null;
    const area = chart.chartArea;
    if (evt.x >= area.left) return null;
    if (evt.y < area.top || evt.y > area.bottom) return null;
    const count = chart.data.labels.length;
    if (!count) return null;
    const index = Math.floor(((evt.y - area.top) / (area.bottom - area.top)) * count);
    return Math.min(Math.max(index, 0), count - 1);
  },

  async refreshFunds() {
    const funds = await apiGet("/api/sinking_funds");
    autoExpandIfEmpty("new-fund-card", funds.length === 0);
    const wrap = document.getElementById("funds-wrap");
    this.renderFundsOverviewChart(funds);
    wrap.innerHTML = funds.map((f) => {
      const pct = f.target_amount > 0 ? Math.min(100, (f.current_amount / f.target_amount) * 100) : 0;
      const contribRows = [...f.contributions].sort((a, b) => (a.date < b.date ? 1 : -1));
      const historyExpanded = this.expandedFundHistoryIds.has(f.id);

      return `
        <div class="fund-card">
          <div class="fund-card-head">
            <h3>${f.name}</h3>
            <div class="fund-card-actions">
              <button class="card-toggle-btn" data-toggle-fund-history="${f.id}" aria-expanded="${historyExpanded ? "true" : "false"}" aria-controls="fund-history-${f.id}">${historyExpanded ? "\u2212" : "+"}</button>
              <button class="btn-ghost" data-delete-fund="${f.id}">Remove</button>
            </div>
          </div>
          <div class="fund-progress-track"><div class="fund-progress-fill" style="width:0%" data-target-pct="${pct}"></div></div>
          <div class="fund-meta">
            <span>${formatCurrency(f.current_amount)} of ${formatCurrency(f.target_amount)} (${pct.toFixed(0)}%)</span>
            <span>${f.target_date ? `Target: ${f.target_date}` : ""}</span>
          </div>

          <div class="fund-history ${historyExpanded ? "" : "collapsed"}" id="fund-history-${f.id}">
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
        </div>
      `;
    }).join("") || `<p class="hint">No sinking funds yet. Create one above.</p>`;

    animateBarFills(wrap.querySelectorAll(".fund-progress-fill"));

    wrap.querySelectorAll("[data-toggle-fund-history]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = parseInt(btn.dataset.toggleFundHistory, 10);
        const historyEl = document.getElementById(`fund-history-${id}`);
        const nowExpanded = historyEl.classList.toggle("collapsed") === false;
        if (nowExpanded) this.expandedFundHistoryIds.add(id);
        else this.expandedFundHistoryIds.delete(id);
        btn.textContent = nowExpanded ? "\u2212" : "+";
        btn.setAttribute("aria-expanded", nowExpanded ? "true" : "false");
      });
    });

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
        const fundId = form.dataset.fundId;
        this.expandedFundHistoryIds.add(parseInt(fundId, 10));
        await apiPost(`/api/sinking_funds/${fundId}/contributions`, {
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
     TAB C - Net Worth Aggregator
     ======================================================================= */
  /**
   * All eight metrics for one account, in a single row, ordered the way
   * you'd actually read them when sizing up an investment: first "how well
   * did this do" (Simple Return -> CAGR -> XIRR -> TWRR, each a
   * progressively more rigorous take on the same question), then "how
   * bumpy was the ride" (Volatility, Max Drawdown, Drawdown Duration,
   * Recovery Time - the last three describe the same single worst decline,
   * so they're kept adjacent). See the backend's
   * _compute_account_return_metrics() and _compute_risk_metrics() for the
   * math. CAGR is flagged as "not cash-flow-adjusted" since it treats
   * every contribution as if it happened on day one; XIRR and TWRR both
   * account for actual dates (XIRR for your money, TWRR for the
   * investment's performance independent of your money).
   */
  renderReturnMetrics(metrics) {
    if (!metrics) return "";
    const fmtPct = (v) => (v === null || v === undefined) ? "N/A" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
    const fmtPlainPct = (v) => (v === null || v === undefined) ? "N/A" : `${v.toFixed(1)}%`;
    const fmtDays = (v) => (v === null || v === undefined) ? "N/A" : `${v} day${v === 1 ? "" : "s"}`;
    const colorFor = (v) => (v === null || v === undefined) ? "var(--text-muted)" : (v < 0 ? "var(--negative)" : "var(--positive)");

    let recovery;
    if (metrics.recovery_days !== null && metrics.recovery_days !== undefined) recovery = fmtDays(metrics.recovery_days);
    else if (metrics.recovered === false) recovery = "Not yet";
    else recovery = "N/A";

    const riskCells = metrics.num_return_periods ? `
        <div><div class="figure-label">TWRR <span class="metric-note" title="Time-Weighted Rate of Return - links each valuation-to-valuation return together with contributions backed out, so it measures investment performance only, not your deposit timing.">(ann.)</span></div><div class="figure-value small" style="color:${colorFor(metrics.twrr_pct)}">${fmtPct(metrics.twrr_pct)}</div></div>
        <div><div class="figure-label">Volatility <span class="metric-note" title="Standard deviation of the periodic returns, annualized. Higher = bumpier.">(ann.)</span></div><div class="figure-value small">${fmtPlainPct(metrics.annualized_volatility_pct)}</div></div>
        <div><div class="figure-label">Max drawdown</div><div class="figure-value small" style="color:${colorFor(metrics.max_drawdown_pct)}">${fmtPlainPct(metrics.max_drawdown_pct)}</div></div>
        <div><div class="figure-label">DD duration</div><div class="figure-value small">${fmtDays(metrics.drawdown_duration_days)}</div></div>
        <div><div class="figure-label">Recovery</div><div class="figure-value small">${recovery}</div></div>
    ` : "";

    return `
      <div class="account-return-metrics">
        <div><div class="figure-label">Simple return</div><div class="figure-value small" style="color:${colorFor(metrics.simple_return_pct)}">${fmtPct(metrics.simple_return_pct)}</div></div>
        <div><div class="figure-label">CAGR <span class="metric-note" title="Not cash-flow-adjusted - treats every contribution as if it happened on day one.">(naive)</span></div><div class="figure-value small" style="color:${colorFor(metrics.cagr_pct)}">${fmtPct(metrics.cagr_pct)}</div></div>
        <div><div class="figure-label">XIRR <span class="metric-note" title="Annualized return, accounting for the actual date of every contribution.">(cash-flow adj.)</span></div><div class="figure-value small" style="color:${colorFor(metrics.xirr_pct)}">${fmtPct(metrics.xirr_pct)}</div></div>
        ${riskCells}
      </div>
    `;
  },

  /**
   * Renders the Investment Insights list (see db_manager.generate_investment_insights)
   * as color-coded badges -- warnings first, then favorable/good flags, then
   * neutral notes (e.g. "not enough history yet"). Used for both a single
   * account's insights and the blended portfolio-level insights.
   */
  renderInsights(insights) {
    return renderInsightBadges(insights);
  },

  async refreshAccounts() {
    const accounts = await apiGet("/api/accounts");
    autoExpandIfEmpty("new-account-card", accounts.length === 0);
    const wrap = document.getElementById("accounts-wrap");

    wrap.innerHTML = accounts.map((acc) => {
      const totalContributed = acc.contributions.reduce((s, c) => s + c.amount, 0);
      const latestValue = acc.valuations.length ? acc.valuations[acc.valuations.length - 1].value : totalContributed;
      const growth = latestValue - totalContributed;
      const contribRows = [...acc.contributions].sort((a, b) => (a.date < b.date ? 1 : -1));
      const valRows = [...acc.valuations].sort((a, b) => (a.date < b.date ? 1 : -1));
      const historyExpanded = this.expandedAccountHistoryIds.has(acc.id);

      return `
        <div class="account-card">
          <div class="account-card-head">
            <h3>${acc.name}</h3>
            <span class="account-type-tag">${acc.account_type}</span>
            <select class="account-risk-profile-select" data-account-id="${acc.id}" title="How much volatility/drawdown is normal for this account -- drives the Investment Insights thresholds">
              <option value="conservative" ${acc.risk_profile === "conservative" ? "selected" : ""}>Risk: Low</option>
              <option value="moderate" ${acc.risk_profile === "moderate" ? "selected" : ""}>Risk: Medium</option>
              <option value="aggressive" ${acc.risk_profile === "aggressive" ? "selected" : ""}>Risk: High</option>
            </select>
            <div class="account-card-actions">
              <button class="card-toggle-btn" data-toggle-history="${acc.id}" aria-expanded="${historyExpanded ? "true" : "false"}" aria-controls="account-history-${acc.id}">${historyExpanded ? "\u2212" : "+"}</button>
              <button class="btn-ghost" data-delete-account="${acc.id}">Remove</button>
            </div>
          </div>
          <div class="account-figures">
            <div><div class="figure-label">Contributed</div><div class="figure-value">${formatCurrency(totalContributed)}</div></div>
            <div><div class="figure-label">Current value</div><div class="figure-value">${formatCurrency(latestValue)}</div></div>
            <div><div class="figure-label">Growth</div><div class="figure-value" style="color:${growth < 0 ? "var(--negative)" : "var(--positive)"}">${formatCurrency(growth)}</div></div>
          </div>
          ${this.renderReturnMetrics(acc.metrics)}
          ${this.renderInsights(acc.insights)}

          <div class="account-history ${historyExpanded ? "" : "collapsed"}" id="account-history-${acc.id}">
            <div class="account-forms">
              <form class="contribution-form" data-account-id="${acc.id}">
                <h3>Contribution</h3>
                <input type="date" name="date" value="${todayISO()}" required />
                <input type="number" step="0.01" name="amount" placeholder="Contribution" required />
                <button type="submit">Log Contribution</button>
              </form>
              <form class="valuation-form" data-account-id="${acc.id}">
                <h3>Current Valuation</h3>
                <input type="date" name="date" value="${todayISO()}" required />
                <input type="number" step="0.01" name="value" placeholder="Current value" required />
                <button type="submit">Update Value</button>
              </form>
              <form class="account-import-form" data-account-id="${acc.id}">
                <h3>Import</h3>
                <input type="file" class="account-import-file" accept=".csv" required />
                <button type="submit">Import CSV</button>
              </form>
            </div>
            <div class="hint account-import-status" id="account-import-status-${acc.id}"></div>

            <div class="account-history-tables">
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
        </div>
      `;
    }).join("") || `<p class="hint">No accounts yet. Add one above.</p>`;

    wrap.querySelectorAll("[data-toggle-history]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = parseInt(btn.dataset.toggleHistory, 10);
        const historyEl = document.getElementById(`account-history-${id}`);
        const nowExpanded = historyEl.classList.toggle("collapsed") === false;
        if (nowExpanded) this.expandedAccountHistoryIds.add(id);
        else this.expandedAccountHistoryIds.delete(id);
        btn.textContent = nowExpanded ? "\u2212" : "+";
        btn.setAttribute("aria-expanded", nowExpanded ? "true" : "false");
      });
    });

    wrap.querySelectorAll(".account-risk-profile-select").forEach((select) => {
      select.addEventListener("change", async () => {
        await apiPut(`/api/accounts/${select.dataset.accountId}`, { risk_profile: select.value });
        await this.refreshAccounts();
      });
    });

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

    wrap.querySelectorAll(".account-import-form").forEach((form) => {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const accountId = form.dataset.accountId;
        const fileInput = form.querySelector(".account-import-file");
        const statusEl = document.getElementById(`account-import-status-${accountId}`);
        const file = fileInput.files[0];
        if (!file) return;

        statusEl.textContent = "Importing...";
        const submitBtn = form.querySelector("button[type=submit]");
        submitBtn.disabled = true;

        try {
          const formData = new FormData();
          formData.append("file", file);
          const res = await fetch(`/api/accounts/${accountId}/import_csv`, { method: "POST", body: formData });
          const result = await res.json();
          if (!res.ok) throw new Error(result.error || `Server returned ${res.status}`);

          const parts = [];
          if (result.imported_contributions) parts.push(`${result.imported_contributions} contribution${result.imported_contributions === 1 ? "" : "s"} imported.`);
          if (result.imported_valuations) parts.push(`${result.imported_valuations} valuation${result.imported_valuations === 1 ? "" : "s"} imported.`);
          if (!result.imported_contributions && !result.imported_valuations) parts.push("No new rows imported.");
          if (result.skipped_duplicate) parts.push(`${result.skipped_duplicate} already imported, skipped.`);
          if (result.errors && result.errors.length) parts.push(`${result.errors.length} row(s) had errors: ${result.errors.slice(0, 3).join(" ")}`);

          // The account card is about to be fully re-rendered by
          // refreshAccounts() below, which wipes out this status message
          // along with everything else - keep it in expandedAccountHistoryIds
          // so the freshly-imported rows are immediately visible instead of
          // requiring another click to expand the section that was just used.
          this.expandedAccountHistoryIds.add(parseInt(accountId, 10));
          await this.refreshAccounts();
          const newStatusEl = document.getElementById(`account-import-status-${accountId}`);
          if (newStatusEl) newStatusEl.textContent = parts.join(" ");
        } catch (err) {
          statusEl.textContent = `Import failed: ${err.message}`;
          submitBtn.disabled = false;
        }
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
          risk_profile: fd.get("risk_profile") || "moderate",
        });
        accountForm.reset();
        await this.refreshAccounts();
      });
    }

    await this.refreshPortfolioInsights();

    this.lastAccounts = accounts;
    this.bindAccountsChartYearNav();
    this.renderAccountsChart(accounts);
  },

  async refreshPortfolioInsights() {
    const card = document.getElementById("portfolio-insights-card");
    const select = document.getElementById("portfolio-risk-profile-select");

    if (!select.dataset.bound) {
      select.dataset.bound = "true";
      let saved = "moderate";
      try {
        const setting = await apiGet("/api/settings/portfolio_risk_profile");
        if (setting && setting.value) saved = setting.value;
      } catch (err) {
        // Fall back to Medium if the setting can't be read.
      }
      select.value = saved;
      select.addEventListener("change", async () => {
        await apiPut("/api/settings/portfolio_risk_profile", { value: select.value });
        await this.refreshPortfolioInsights();
      });
    }

    const portfolio = await apiGet("/api/portfolio/metrics");
    if (!portfolio) {
      card.style.display = "none";
      return;
    }
    card.style.display = "";
    document.getElementById("portfolio-metrics-figures").innerHTML = this.renderReturnMetrics(portfolio);
    document.getElementById("portfolio-insights-list").innerHTML = this.renderInsights(portfolio.insights) || `<p class="hint">No flags for the blended portfolio at this risk profile.</p>`;
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
          x: { ticks: { color: "#93A0AF", autoSkip: false, callback: monthOnlyTickCallback(dates) }, grid: { color: "#28323F" } },
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

  /* =======================================================================
     TAB D - Debt Payoff Tracker
     ======================================================================= */
  async refreshDebts() {
    const debts = await apiGet("/api/debts");
    autoExpandIfEmpty("new-debt-card", debts.length === 0);
    const wrap = document.getElementById("debts-wrap");

    const withSummaries = await Promise.all(debts.map(async (d) => {
      try {
        const summary = await apiGet(`/api/debts/${d.id}/summary`);
        return { ...d, summary };
      } catch (err) {
        return { ...d, summary: null };
      }
    }));
    this.lastDebts = withSummaries;

    wrap.innerHTML = withSummaries.map((debt) => {
      const historyExpanded = this.expandedDebtHistoryIds.has(debt.id);
      const payments = [...debt.payments].sort((a, b) => (a.date < b.date ? 1 : -1));
      const s = debt.summary || {};

      let payoffFigure;
      if (debt.current_balance <= 0.01) {
        payoffFigure = `<div><div class="figure-label">Status</div><div class="figure-value small" style="color:var(--positive)">Paid off</div></div>`;
      } else if (s.warning) {
        payoffFigure = `<div><div class="figure-label">Projected payoff</div><div class="figure-value small" style="color:var(--negative)">Won't pay off</div></div>`;
      } else {
        payoffFigure = `<div><div class="figure-label">Projected payoff</div><div class="figure-value small">${s.payoff_date || "N/A"} (${s.months_to_payoff ?? "N/A"} mo)</div></div>`;
      }

      return `
        <div class="account-card">
          <div class="account-card-head">
            <h3>${debt.name}</h3>
            <span class="account-type-tag">${debt.debt_type}${debt.is_revolving ? " &middot; revolving" : ""}</span>
            <div class="account-card-actions">
              <button class="card-toggle-btn" data-toggle-debt-history="${debt.id}" aria-expanded="${historyExpanded ? "true" : "false"}" aria-controls="debt-history-${debt.id}">${historyExpanded ? "\u2212" : "+"}</button>
              <button class="btn-ghost" data-delete-debt="${debt.id}">Remove</button>
            </div>
          </div>
          <div class="account-figures">
            <div><div class="figure-label">Balance</div><div class="figure-value">${formatCurrency(debt.current_balance)}</div></div>
            <div><div class="figure-label">APR</div><div class="figure-value small">${Number(debt.apr).toFixed(2)}%</div></div>
            <div><div class="figure-label">Min payment</div><div class="figure-value small">${formatCurrency(debt.minimum_payment)}</div></div>
            ${payoffFigure}
          </div>
          ${s.warning ? `<p class="hint" style="color:var(--negative)">${s.warning}</p>` : ""}
          ${(!s.warning && s.total_interest != null && debt.current_balance > 0.01) ? `<p class="hint">Projected to pay ${formatCurrency(s.total_interest)} in interest at the current minimum payment.</p>` : ""}

          <div class="account-history ${historyExpanded ? "" : "collapsed"}" id="debt-history-${debt.id}">
            <div class="account-forms">
              <form class="debt-payment-form" data-debt-id="${debt.id}">
                <input type="date" name="date" value="${todayISO()}" required />
                <input type="number" step="0.01" name="amount" placeholder="Payment amount" required />
                <button type="submit">Log payment</button>
              </form>
            </div>
            <table class="ledger-table">
              <thead><tr><th>Date</th><th>Amount</th><th>Principal</th><th>Interest</th><th></th></tr></thead>
              <tbody>
                ${payments.length ? payments.map((p) => `
                  <tr>
                    <td>${p.date}</td>
                    <td class="num">${formatCurrency(p.amount)}</td>
                    <td class="num">${formatCurrency(p.principal)}</td>
                    <td class="num">${formatCurrency(p.interest)}</td>
                    <td><button class="btn-ghost" data-delete-debt-payment="${p.id}">Delete</button></td>
                  </tr>
                `).join("") : `<tr><td colspan="5" class="hint">No payments logged yet.</td></tr>`}
              </tbody>
            </table>
          </div>
        </div>
      `;
    }).join("") || `<p class="hint">No debts yet. Add one above.</p>`;

    wrap.querySelectorAll("[data-toggle-debt-history]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = parseInt(btn.dataset.toggleDebtHistory, 10);
        const historyEl = document.getElementById(`debt-history-${id}`);
        const nowExpanded = historyEl.classList.toggle("collapsed") === false;
        if (nowExpanded) this.expandedDebtHistoryIds.add(id);
        else this.expandedDebtHistoryIds.delete(id);
        btn.textContent = nowExpanded ? "\u2212" : "+";
        btn.setAttribute("aria-expanded", nowExpanded ? "true" : "false");
      });
    });

    wrap.querySelectorAll("[data-delete-debt]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Remove this debt and its entire payment history? This can't be undone.")) return;
        await apiDelete(`/api/debts/${btn.dataset.deleteDebt}`);
        await this.refreshDebts();
      });
    });

    wrap.querySelectorAll("[data-delete-debt-payment]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/debt_payments/${btn.dataset.deleteDebtPayment}`);
        await this.refreshDebts();
      });
    });

    wrap.querySelectorAll(".debt-payment-form").forEach((form) => {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const debtId = form.dataset.debtId;
        // Same reasoning as the Net Worth account import: keep the section
        // expanded across the refresh below so the just-logged payment is
        // immediately visible instead of needing a second click.
        this.expandedDebtHistoryIds.add(parseInt(debtId, 10));
        await apiPost(`/api/debts/${debtId}/payments`, {
          date: fd.get("date"),
          amount: parseFloat(fd.get("amount")) || 0,
        });
        await this.refreshDebts();
      });
    });

    this.bindDebtForm();
    this.bindPayoffPlanForm();
  },

  bindDebtForm() {
    const form = document.getElementById("debt-form");
    if (form.dataset.bound) return;
    form.dataset.bound = "true";
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      await apiPost("/api/debts", {
        name: fd.get("name"),
        debt_type: fd.get("debt_type"),
        is_revolving: fd.get("is_revolving") === "on",
        current_balance: parseFloat(fd.get("current_balance")) || 0,
        apr: parseFloat(fd.get("apr")) || 0,
        minimum_payment: parseFloat(fd.get("minimum_payment")) || 0,
        escrow_amount: parseFloat(fd.get("escrow_amount")) || 0,
      });
      form.reset();
      await this.refreshDebts();
    });
  },

  /**
   * Snowball vs. Avalanche payoff simulation across every debt at once -
   * see get_debt_payoff_plan() on the backend for the actual math.
   */
  bindPayoffPlanForm() {
    const form = document.getElementById("payoff-plan-form");
    const exportBtn = document.getElementById("payoff-plan-export-btn");
    if (form.dataset.bound) return;
    form.dataset.bound = "true";

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const strategy = document.getElementById("payoff-strategy").value;
      const extra = parseFloat(document.getElementById("payoff-extra-monthly").value) || 0;
      const plan = await apiGet(`/api/debt_payoff_plan?strategy=${strategy}&extra_monthly=${extra}`);
      this.lastPayoffPlan = plan;
      this.renderPayoffPlan(plan);
    });

    exportBtn.addEventListener("click", async () => {
      if (!this.lastPayoffPlan) return;
      const debtById = new Map((this.lastDebts || []).map((d) => [d.id, d]));
      const headers = ["Debt", "Monthly Payment", "Amount Paid Off", "Remaining Balance", "Paid off in (month #)"];
      const rows = this.lastPayoffPlan.payoff_order.map((p) => {
        const debt = debtById.get(p.id);
        const amountPaid = debt ? debt.payments.reduce((s, pay) => s + (pay.principal || 0), 0) : null;
        return [
          p.name,
          debt ? debt.minimum_payment : null,
          amountPaid,
          debt ? debt.current_balance : null,
          p.month,
        ];
      });
      await exportRowsAsExcel(headers, rows, "debt_payoff_plan", "Payoff Plan");
    });
  },

  renderPayoffPlan(plan) {
    const summaryEl = document.getElementById("payoff-plan-summary");
    const table = document.getElementById("payoff-plan-table");
    const tbody = table.querySelector("tbody");
    const exportBtn = document.getElementById("payoff-plan-export-btn");

    if (plan.months_to_debt_free === 0 && !plan.payoff_order.length) {
      summaryEl.textContent = "No debts to pay off - you're debt-free!";
      table.style.display = "none";
      exportBtn.style.display = "none";
      return;
    }

    if (plan.months_to_debt_free === null) {
      summaryEl.textContent = "At this payment level, at least one debt's minimum payment doesn't cover its own interest - it'll never pay off. Try increasing the extra monthly amount.";
      table.style.display = "none";
      exportBtn.style.display = "none";
      return;
    }

    const years = Math.floor(plan.months_to_debt_free / 12);
    const months = plan.months_to_debt_free % 12;
    const timeStr = [years ? `${years}y` : "", months ? `${months}mo` : ""].filter(Boolean).join(" ") || "0mo";

    summaryEl.innerHTML = `Debt-free in <strong>${timeStr}</strong> (${plan.months_to_debt_free} months), paying about <strong>${formatCurrency(plan.total_interest)}</strong> in total interest under the ${plan.strategy} strategy.`;
    table.style.display = "";

    const debtById = new Map((this.lastDebts || []).map((d) => [d.id, d]));
    tbody.innerHTML = plan.payoff_order.map((p) => {
      const debt = debtById.get(p.id);
      const amountPaid = debt ? debt.payments.reduce((s, pay) => s + (pay.principal || 0), 0) : null;
      return `
        <tr>
          <td>${p.name}</td>
          <td class="num">${debt ? formatCurrency(debt.minimum_payment) : "N/A"}</td>
          <td class="num">${debt ? formatCurrency(amountPaid) : "N/A"}</td>
          <td class="num">${debt ? formatCurrency(debt.current_balance) : "N/A"}</td>
          <td class="num">Month ${p.month}</td>
        </tr>
      `;
    }).join("");
    exportBtn.style.display = "";
  },
};