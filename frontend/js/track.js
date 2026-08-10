/* =========================================================================
   track.js — Page 2: Track (three sub-tabs)
   ========================================================================= */

const Track = {
  lineItemsForMonth: [],
  accountsChart: null,

  async refresh() {
    if (document.getElementById("page-track").classList.contains("active") === false) return;
    await this.refreshLedger();
  },

  /* =======================================================================
     TAB A — Daily Ledger
     ======================================================================= */
  async refreshLedger() {
    this.lineItemsForMonth = await apiGet(`/api/budget/line_items?month=${App.currentMonth}`);
    this.populateLineItemSelect();
    await this.renderLedgerSummary();
    await this.renderTransactions();
    this.bindLedgerForm();
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

  async renderTransactions() {
    const txs = await apiGet(`/api/transactions?month=${App.currentMonth}`);
    const itemsById = Object.fromEntries(this.lineItemsForMonth.map((li) => [li.id, li]));

    const tbody = document.querySelector("#transactions-table tbody");
    tbody.innerHTML = txs.map((tx) => {
      const li = itemsById[tx.line_item_id];
      return `
        <tr>
          <td>${tx.date}</td>
          <td>${li ? `${li.group_name} &rsaquo; ${li.name}` : "&mdash;"}</td>
          <td>${tx.description || ""}</td>
          <td class="num">${formatCurrency(tx.amount)}</td>
          <td><button class="btn-ghost" data-delete-tx="${tx.id}">&times;</button></td>
        </tr>
      `;
    }).join("") || `<tr><td colspan="5" class="hint">No transactions logged yet.</td></tr>`;

    tbody.querySelectorAll("[data-delete-tx]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/transactions/${btn.dataset.deleteTx}`);
        await this.renderLedgerSummary();
        await this.renderTransactions();
      });
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
          <div class="fund-progress-track"><div class="fund-progress-fill" style="width:${pct}%"></div></div>
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

    this.renderAccountsChart(accounts);
  },

  renderAccountsChart(accounts) {
    const card = document.getElementById("networth-chart-card");
    if (!accounts.length) {
      card.style.display = "none";
      return;
    }

    // Union of every date any account has a contribution or valuation on.
    const allDates = new Set();
    accounts.forEach((acc) => {
      acc.contributions.forEach((c) => allDates.add(c.date));
      acc.valuations.forEach((v) => allDates.add(v.date));
    });
    const dates = [...allDates].sort();

    if (!dates.length) {
      card.style.display = "none";
      return;
    }
    card.style.display = "";

    const palette = ["#C7A15C", "#74A788", "#7A93B0", "#C3654D", "#B9A5D6", "#7FC1C6", "#D8B679", "#9FB3C8"];

    // For each account, value at a given date = latest valuation on/before that
    // date, or (if none logged yet) the running contribution total as a stand-in.
    const datasets = accounts.map((acc, i) => ({
      label: acc.name,
      data: dates.map((d) => {
        const applicableVals = acc.valuations.filter((v) => v.date <= d);
        if (applicableVals.length) return applicableVals[applicableVals.length - 1].value;
        const contribs = acc.contributions.filter((c) => c.date <= d);
        return contribs.reduce((s, c) => s + c.amount, 0);
      }),
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
};