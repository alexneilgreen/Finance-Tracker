/* =========================================================================
   budget.js — Page 1: Budget
   Handles Income -> Deductions -> Investments -> Net Take-Home, and the
   zero-based assignment of that Net Take-Home across groups/line items.
   ========================================================================= */

const Budget = {
  groups: [],
  lineItems: [],
  incomeSummary: null,

  async refresh() {
    if (document.getElementById("page-budget").classList.contains("active") === false) return;
    await this.loadPaySchedule();
    await this.loadIncome();
    await this.loadGroupsAndItems();
    await this.loadPresets();
    this.bindForms();
    this.bindPresetToolbar();
    this.bindPayScheduleForms();
  },

  /* ------------------------- Income strip ------------------------- */
  async loadIncome() {
    this.incomeSummary = await apiGet(`/api/income?month=${App.currentMonth}`);
    this.renderIncome();
  },

  renderIncome() {
    const { income, total_deductions, total_investments, total_match, net_take_home, schedule } = this.incomeSummary;

    // ---- Income sources (editable amount, deletable) ----
    const incomeList = document.getElementById("income-list");
    const scheduleIncomeRow = schedule
      ? `<div class="row schedule-row"><span class="row-name">Paycheck income (&times;${schedule.payment_count})</span><span class="amt">${formatCurrency(schedule.gross)}</span></div>`
      : "";
    incomeList.innerHTML = scheduleIncomeRow + (income.map((r) => `
      <div class="row" data-income-id="${r.id}">
        <span class="row-name">${r.source}</span>
        <input type="number" step="0.01" class="edit-income-amount" value="${r.gross_amount}" data-id="${r.id}" />
        <button class="btn-ghost" data-delete-income="${r.id}">&times;</button>
      </div>
    `).join("") || (schedule ? "" : `<div class="row"><span class="hint">No income logged yet</span></div>`));

    // ---- Deductions (flattened across all income sources this month, editable/deletable) ----
    const allDeductions = income.flatMap((r) => r.deductions);
    const deductionsList = document.getElementById("deductions-list");
    const scheduleDeductionRow = schedule && schedule.total_deductions > 0
      ? `<div class="row schedule-row"><span class="row-name">Pay schedule (&times;${schedule.payment_count})</span><span class="amt">${formatCurrency(schedule.total_deductions)}</span></div>`
      : "";
    deductionsList.innerHTML = scheduleDeductionRow + (allDeductions.map((d) => `
      <div class="row" data-deduction-id="${d.id}">
        <span class="row-name">${d.name}</span>
        <input type="number" step="0.01" class="edit-deduction-amount" value="${d.amount}" data-id="${d.id}" data-name="${d.name}" />
        <button class="btn-ghost" data-delete-deduction="${d.id}">&times;</button>
      </div>
    `).join("") || (scheduleDeductionRow ? "" : `<div class="row"><span class="hint">None yet</span></div>`));
    deductionsList.insertAdjacentHTML("beforeend",
      `<div class="row"><span class="row-name"><strong>Total</strong></span><span class="amt">${formatCurrency(total_deductions)}</span></div>`);

    // ---- Investments (employee contributions + employer match, editable/deletable) ----
    const allInvestments = income.flatMap((r) => r.investments);
    const investmentsList = document.getElementById("investments-list");
    const scheduleInvestmentRow = schedule && (schedule.total_investments > 0 || schedule.total_match > 0)
      ? `<div class="row schedule-row"><span class="row-name">Pay schedule (&times;${schedule.payment_count})</span><span class="amt">${formatCurrency(schedule.total_investments)}</span></div>`
      : "";
    investmentsList.innerHTML = scheduleInvestmentRow + (allInvestments.map((inv) => `
      <div class="row ${inv.is_match ? "match-row" : ""}" data-investment-id="${inv.id}">
        <span class="row-name">${inv.name}</span>
        <input type="number" step="0.01" class="edit-investment-amount" value="${inv.amount}" data-id="${inv.id}" data-name="${inv.name}" data-match="${inv.is_match ? "1" : "0"}" />
        <button class="btn-ghost" data-delete-investment="${inv.id}">&times;</button>
      </div>
    `).join("") || (scheduleInvestmentRow ? "" : `<div class="row"><span class="hint">None yet</span></div>`));
    investmentsList.insertAdjacentHTML("beforeend",
      `<div class="row"><span class="row-name"><strong>Total (subtracted)</strong></span><span class="amt">${formatCurrency(total_investments)}</span></div>`);

    document.getElementById("match-total").textContent =
      total_match > 0 ? `+ ${formatCurrency(total_match)} employer match logged (not subtracted from Net Take-Home)` : "";

    document.getElementById("net-take-home-figure").textContent = formatCurrency(net_take_home);

    const monthInfo = document.getElementById("pay-schedule-month-info");
    monthInfo.textContent = schedule
      ? `${schedule.payment_count} paycheck${schedule.payment_count === 1 ? "" : "s"} this month (${formatCurrency(schedule.per_check_gross)} each)`
      : "Set an annual income and a known pay date to get started";

    // Store the most recent income id so new deduction/investment entries know where to attach.
    this.latestIncomeId = income.length ? income[income.length - 1].id : null;

    this.bindIncomeListHandlers();
  },

  /* ------------------------- Pay Schedule ------------------------- */
  async loadPaySchedule() {
    this.paySchedule = await apiGet("/api/pay_schedule");
    this.renderPaySchedule();
  },

  renderPaySchedule() {
    const s = this.paySchedule;
    document.getElementById("ps-annual-income").value = s ? s.annual_income : "";
    document.getElementById("ps-anchor-date").value = s ? s.anchor_date || "" : "";
    document.getElementById("ps-payments-per-year").value = s ? s.payments_per_year : 26;

    const dedList = document.getElementById("ps-deductions-list");
    const deductions = s ? s.deductions : [];
    dedList.innerHTML = deductions.map((d) => `
      <div class="row" data-id="${d.id}">
        <span class="row-name">${d.name}</span>
        <input type="number" step="0.01" class="edit-ps-deduction" value="${d.amount}" data-id="${d.id}" data-name="${d.name}" />
        <button class="btn-ghost" data-delete-ps-deduction="${d.id}">&times;</button>
      </div>
    `).join("") || `<div class="row"><span class="hint">None yet</span></div>`;

    const invList = document.getElementById("ps-investments-list");
    const investments = s ? s.investments : [];
    invList.innerHTML = investments.map((inv) => `
      <div class="row ${inv.is_match ? "match-row" : ""}" data-id="${inv.id}">
        <span class="row-name">${inv.name}</span>
        <input type="number" step="0.01" class="edit-ps-investment" value="${inv.amount}" data-id="${inv.id}" data-name="${inv.name}" data-match="${inv.is_match ? "1" : "0"}" />
        <button class="btn-ghost" data-delete-ps-investment="${inv.id}">&times;</button>
      </div>
    `).join("") || `<div class="row"><span class="hint">None yet</span></div>`;

    dedList.querySelectorAll(".edit-ps-deduction").forEach((input) => {
      input.addEventListener("change", async (e) => {
        await apiPut(`/api/pay_schedule/deductions/${e.target.dataset.id}`, {
          name: e.target.dataset.name,
          amount: parseFloat(e.target.value) || 0,
        });
        await this.loadPaySchedule();
        await this.loadIncome();
      });
    });
    dedList.querySelectorAll("[data-delete-ps-deduction]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/pay_schedule/deductions/${btn.dataset.deletePsDeduction}`);
        await this.loadPaySchedule();
        await this.loadIncome();
      });
    });

    invList.querySelectorAll(".edit-ps-investment").forEach((input) => {
      input.addEventListener("change", async (e) => {
        await apiPut(`/api/pay_schedule/investments/${e.target.dataset.id}`, {
          name: e.target.dataset.name,
          amount: parseFloat(e.target.value) || 0,
          is_match: e.target.dataset.match === "1",
        });
        await this.loadPaySchedule();
        await this.loadIncome();
      });
    });
    invList.querySelectorAll("[data-delete-ps-investment]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/pay_schedule/investments/${btn.dataset.deletePsInvestment}`);
        await this.loadPaySchedule();
        await this.loadIncome();
      });
    });
  },

  bindPayScheduleForms() {
    if (this._psBound) return;
    this._psBound = true;

    document.getElementById("pay-schedule-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      await apiPut("/api/pay_schedule", {
        annual_income: parseFloat(document.getElementById("ps-annual-income").value) || 0,
        anchor_date: document.getElementById("ps-anchor-date").value || null,
        payments_per_year: parseInt(document.getElementById("ps-payments-per-year").value, 10) || 26,
      });
      await this.loadPaySchedule();
      await this.loadIncome();
      this.renderAssignTotals();
    });

    document.getElementById("ps-deduction-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      await apiPost("/api/pay_schedule/deductions", {
        name: fd.get("name"),
        amount: parseFloat(fd.get("amount")) || 0,
      });
      e.target.reset();
      await this.loadPaySchedule();
      await this.loadIncome();
      this.renderAssignTotals();
    });

    document.getElementById("ps-investment-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      await apiPost("/api/pay_schedule/investments", {
        name: fd.get("name"),
        amount: parseFloat(fd.get("amount")) || 0,
        is_match: fd.get("is_match") === "on",
      });
      e.target.reset();
      await this.loadPaySchedule();
      await this.loadIncome();
      this.renderAssignTotals();
    });
  },

  bindIncomeListHandlers() {
    document.querySelectorAll(".edit-income-amount").forEach((input) => {
      input.addEventListener("change", async (e) => {
        await apiPut(`/api/income/${e.target.dataset.id}`, {
          gross_amount: parseFloat(e.target.value) || 0,
        });
        await this.loadIncome();
        this.renderAssignTotals();
      });
    });

    document.querySelectorAll("[data-delete-income]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/income/${btn.dataset.deleteIncome}`);
        await this.loadIncome();
        this.renderAssignTotals();
      });
    });

    document.querySelectorAll(".edit-deduction-amount").forEach((input) => {
      input.addEventListener("change", async (e) => {
        await apiPut(`/api/deductions/${e.target.dataset.id}`, {
          name: e.target.dataset.name,
          amount: parseFloat(e.target.value) || 0,
        });
        await this.loadIncome();
        this.renderAssignTotals();
      });
    });
    document.querySelectorAll("[data-delete-deduction]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/deductions/${btn.dataset.deleteDeduction}`);
        await this.loadIncome();
        this.renderAssignTotals();
      });
    });

    document.querySelectorAll(".edit-investment-amount").forEach((input) => {
      input.addEventListener("change", async (e) => {
        await apiPut(`/api/investments/${e.target.dataset.id}`, {
          name: e.target.dataset.name,
          amount: parseFloat(e.target.value) || 0,
          is_match: e.target.dataset.match === "1",
        });
        await this.loadIncome();
        this.renderAssignTotals();
      });
    });
    document.querySelectorAll("[data-delete-investment]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/investments/${btn.dataset.deleteInvestment}`);
        await this.loadIncome();
        this.renderAssignTotals();
      });
    });
  },

  /* ------------------------- Groups & line items ------------------------- */
  async loadGroupsAndItems() {
    this.groups = await apiGet("/api/budget/groups");
    this.lineItems = await apiGet(`/api/budget/line_items?month=${App.currentMonth}`);
    this.renderGroups();
    this.renderAssignTotals();
  },

  renderGroups() {
    const wrap = document.getElementById("budget-groups-wrap");
    wrap.innerHTML = "";

    this.groups.forEach((group) => {
      const items = this.lineItems.filter((li) => li.group_id === group.id);
      const groupTotal = items.reduce((sum, li) => sum + li.planned_amount, 0);

      const el = document.createElement("div");
      el.className = "budget-group";
      el.innerHTML = `
        <div class="budget-group-head">
          <h3>${group.name}</h3>
          <span class="num">${formatCurrency(groupTotal)}</span>
          <button class="btn-ghost" data-delete-group="${group.id}">Remove</button>
        </div>
        <div class="budget-group-body">
          ${items.map((li) => `
            <div class="line-item-row" data-item-id="${li.id}">
              <span>${li.name}</span>
              <input type="number" step="0.01" class="planned-input" value="${li.planned_amount}" data-item-id="${li.id}" />
              <button class="btn-ghost" data-delete-item="${li.id}">&times;</button>
            </div>
          `).join("")}
          <form class="line-item-form" data-group-id="${group.id}">
            <input type="text" name="name" placeholder="Line item name" required />
            <input type="number" step="0.01" name="planned_amount" placeholder="0.00" required />
            <button type="submit">Add</button>
          </form>
        </div>
      `;
      wrap.appendChild(el);
    });

    // Line item planned-amount edits (blur = save)
    wrap.querySelectorAll(".planned-input").forEach((input) => {
      input.addEventListener("change", async (e) => {
        await apiPut(`/api/budget/line_items/${e.target.dataset.itemId}`, {
          planned_amount: parseFloat(e.target.value) || 0,
        });
        await this.loadGroupsAndItems();
      });
    });

    // Delete line item
    wrap.querySelectorAll("[data-delete-item]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/budget/line_items/${btn.dataset.deleteItem}`);
        await this.loadGroupsAndItems();
      });
    });

    // Delete group
    wrap.querySelectorAll("[data-delete-group]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/budget/groups/${btn.dataset.deleteGroup}`);
        await this.loadGroupsAndItems();
      });
    });

    // Add line item within a group
    wrap.querySelectorAll(".line-item-form").forEach((form) => {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        await apiPost("/api/budget/line_items", {
          group_id: parseInt(form.dataset.groupId, 10),
          name: fd.get("name"),
          planned_amount: parseFloat(fd.get("planned_amount")) || 0,
          month: App.currentMonth,
        });
        await this.loadGroupsAndItems();
      });
    });
  },

  renderAssignTotals() {
    const assigned = this.lineItems.reduce((sum, li) => sum + li.planned_amount, 0);
    const netTakeHome = this.incomeSummary ? this.incomeSummary.net_take_home : 0;
    const unassigned = netTakeHome - assigned;

    document.getElementById("assigned-total").textContent = formatCurrency(assigned);
    const unassignedEl = document.getElementById("unassigned-total");
    unassignedEl.textContent = formatCurrency(unassigned);
    unassignedEl.style.color = unassigned < 0 ? "var(--negative)" : "var(--positive)";
  },

  /* ------------------------- Forms (bound once) ------------------------- */
  bindForms() {
    if (this._bound) return;
    this._bound = true;

    document.getElementById("group-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      await apiPost("/api/budget/groups", { name: fd.get("name"), sort_order: this.groups.length });
      e.target.reset();
      await this.loadGroupsAndItems();
    });
  },

  /* ------------------------- Budget Presets ------------------------- */
  async loadPresets() {
    this.presets = await apiGet("/api/budget/presets");
    const select = document.getElementById("preset-select");
    const currentValue = select.value;
    select.innerHTML =
      `<option value="">Load a preset&hellip;</option>` +
      this.presets.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
    // Preserve the selection across a refresh if that preset still exists.
    if (this.presets.some((p) => String(p.id) === currentValue)) {
      select.value = currentValue;
    }
  },

  bindPresetToolbar() {
    if (this._presetToolbarBound) return;
    this._presetToolbarBound = true;

    const select = document.getElementById("preset-select");
    const applyBtn = document.getElementById("preset-apply-btn");
    const deleteBtn = document.getElementById("preset-delete-btn");
    const saveBtn = document.getElementById("preset-save-btn");

    // Selecting an option only sets the selection — applying and deleting
    // are separate, explicit actions below, so choosing a preset can never
    // itself trigger (or accidentally block) either one.
    applyBtn.addEventListener("click", async () => {
      const presetId = select.value;
      if (!presetId) {
        alert("Select a preset from the dropdown first.");
        return;
      }
      const preset = this.presets.find((p) => String(p.id) === presetId);
      const label = preset ? preset.name : "this preset";

      const confirmed = confirm(
        `Apply "${label}" to ${App.currentMonth}? This replaces every line item currently set for that month.`
      );
      if (!confirmed) return;

      try {
        await apiPost(`/api/budget/presets/${presetId}/apply`, { month: App.currentMonth });
        await this.loadIncome();
        await this.loadGroupsAndItems();
        select.value = "";
      } catch (err) {
        alert(`Couldn't apply that preset: ${err.message}`);
      }
    });

    deleteBtn.addEventListener("click", async () => {
      const presetId = select.value;
      if (!presetId) {
        alert("Select a preset from the dropdown first.");
        return;
      }
      const preset = this.presets.find((p) => String(p.id) === presetId);
      const label = preset ? preset.name : "this preset";
      if (!confirm(`Delete the preset "${label}"? This can't be undone.`)) return;

      await apiDelete(`/api/budget/presets/${presetId}`);
      await this.loadPresets();
    });

    saveBtn.addEventListener("click", () => this.openSavePresetModal());
    this.bindSavePresetModal();
  },

  openSavePresetModal() {
    const modal = document.getElementById("save-preset-modal");
    const nameInput = document.getElementById("save-preset-name");
    const statusEl = document.getElementById("save-preset-status");
    nameInput.value = "";
    statusEl.textContent = "";
    modal.classList.add("open");
    nameInput.focus();
  },

  bindSavePresetModal() {
    const modal = document.getElementById("save-preset-modal");
    const nameInput = document.getElementById("save-preset-name");
    const statusEl = document.getElementById("save-preset-status");
    const cancelBtn = document.getElementById("save-preset-cancel");
    const confirmBtn = document.getElementById("save-preset-confirm");

    const close = () => modal.classList.remove("open");
    cancelBtn.addEventListener("click", close);
    modal.addEventListener("click", (e) => {
      if (e.target === modal) close();
    });

    confirmBtn.addEventListener("click", async () => {
      const name = nameInput.value.trim();
      if (!name) {
        statusEl.textContent = "Enter a name for this preset.";
        return;
      }
      confirmBtn.disabled = true;
      statusEl.textContent = "Saving...";
      try {
        const res = await fetch("/api/budget/presets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, month: App.currentMonth }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `Server returned ${res.status}`);
        await this.loadPresets();
        close();
      } catch (err) {
        statusEl.textContent = err.message;
      } finally {
        confirmBtn.disabled = false;
      }
    });
  },
};