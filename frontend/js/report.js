/* =========================================================================
   report.js - Page 3: Report
   Uses Chart.js (loaded via CDN in index.html) for the donut and line
   charts. The donut is drawn as two concentric rings (budgeted / spent),
   each summing to the full Net Take-Home via an "Unbudgeted"/"Unspent"
   remainder slice. The "Zero-Based Budget Flow" is a hand-rolled SVG
   Sankey diagram - no extra CDN dependency, so it works the same
   whether or not the machine has internet access after first launch.
   ========================================================================= */

const Report = {
  donutChart: null,
  trendChart: null,
  netWorthChart: null,
  multiYearChart: null,
  savingsRateChart: null,
  lastAdherenceRows: null,
  lastMultiYearData: null,
  lastSavingsRateData: null,
  palette: ["#C7A15C", "#74A788", "#7A93B0", "#C3654D", "#B9A5D6", "#7FC1C6", "#D8B679", "#9FB3C8"],
  mutedColor: "#3A4552",

  async refresh() {
    this.syncReportYearFromGlobalMonth();
    this.bindReportYearControl();
    this.bindReportSubNav();
    this.bindExportButtons();
    await this.refreshActiveSubpage();
  },

  /**
   * Report Year defaults to whatever year the global Ledger Month is
   * currently on - re-synced every time Report.refresh() runs (i.e. every
   * time the Ledger Month changes, or the Report tab is opened). It can
   * still be moved independently afterward without touching Ledger Month;
   * that manual choice just doesn't survive the *next* Ledger Month change.
   */
  syncReportYearFromGlobalMonth() {
    const input = document.getElementById("report-year");
    input.value = App.currentMonth ? parseInt(App.currentMonth.slice(0, 4), 10) : new Date().getFullYear();
  },

  bindReportYearControl() {
    const input = document.getElementById("report-year");
    if (input.dataset.bound) return;
    input.dataset.bound = "true";
    input.addEventListener("change", () => this.refreshActiveSubpage());

    document.getElementById("report-year-prev").addEventListener("click", () => {
      input.value = (parseInt(input.value, 10) || new Date().getFullYear()) - 1;
      this.refreshActiveSubpage();
    });
    document.getElementById("report-year-next").addEventListener("click", () => {
      input.value = (parseInt(input.value, 10) || new Date().getFullYear()) + 1;
      this.refreshActiveSubpage();
    });
  },

  /**
   * This Month / Annual / Investments / Tax sub-tabs - same pattern as
   * Track's subnav, but its own class names so the two never collide, and
   * each sub-tab's cards only render when that sub-tab is actually active
   * (mirrors Track.refreshLedger/refreshFunds/etc. only firing for the
   * tab being switched to).
   */
  bindReportSubNav() {
    const tabs = document.querySelectorAll(".report-subnav-tab");
    if (tabs[0] && tabs[0].dataset.bound) return;
    tabs.forEach((tab) => {
      tab.dataset.bound = "true";
      tab.addEventListener("click", () => {
        tabs.forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        document.querySelectorAll(".report-subpage").forEach((p) => p.classList.remove("active"));
        document.getElementById(`report-subpage-${tab.dataset.reportSubpage}`).classList.add("active");
        this.refreshActiveSubpage();
      });
    });
  },

  async refreshActiveSubpage() {
    const activeTab = document.querySelector(".report-subnav-tab.active");
    const sub = activeTab ? activeTab.dataset.reportSubpage : "thismonth";

    if (sub === "thismonth") {
      await this.renderMonthlySpending();
      await this.renderAdherence();
      await this.renderBudgetFlow();
    } else if (sub === "annual") {
      await this.renderAnnualTrend();
      await this.renderMultiYearComparison();
      await this.renderSavingsRate();
    } else if (sub === "investments") {
      await this.renderNetWorthHistory();
      await this.renderInvestmentInsights();
    } else if (sub === "tax") {
      await this.renderTaxSummary();
    }
  },

  /* ---- Monthly spending: two-ring donut ---- */
  async renderMonthlySpending() {
    const data = await apiGet(`/api/report/monthly_spending?month=${App.currentMonth}`);
    const income = await apiGet(`/api/income?month=${App.currentMonth}`);
    const netTakeHome = income.net_take_home || 0;

    const labels = data.map((g) => g.group);
    const colors = data.map((_, i) => this.palette[i % this.palette.length]);

    const plannedSum = data.reduce((s, g) => s + g.planned, 0);
    const spentSum = data.reduce((s, g) => s + g.spent, 0);
    const unbudgeted = Math.max(0, netTakeHome - plannedSum);
    const unspent = Math.max(0, netTakeHome - spentSum);

    const budgetedRing = [...data.map((g) => g.planned), unbudgeted];
    const spentRing = [...data.map((g) => g.spent), unspent];
    const ringLabels = [...labels, "Unbudgeted / Unspent"];
    const ringColors = [...colors, this.mutedColor];

    const ctx = document.getElementById("spending-donut");
    if (this.donutChart) this.donutChart.destroy();
    this.donutChart = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: ringLabels,
        datasets: [
          {
            // Inner ring: what you planned to spend
            label: "Budgeted",
            data: budgetedRing,
            backgroundColor: ringColors,
            borderColor: "#161D26",
            borderWidth: 2,
            weight: 1,
          },
          {
            // Outer ring: what you've actually spent
            label: "Spent",
            data: spentRing,
            backgroundColor: ringColors,
            borderColor: "#161D26",
            borderWidth: 2,
            weight: 1,
          },
        ],
      },
      options: {
        cutout: "35%",
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => `${item.dataset.label}: ${item.label} - ${formatCurrency(item.raw)}`,
            },
          },
        },
      },
    });

    // Manual legend so it's unambiguous which ring is which, and ties colors to group names.
    const legendWrap = document.getElementById("donut-legend");
    legendWrap.innerHTML = [
      `<span><strong>Net Take-Home:</strong> ${formatCurrency(netTakeHome)}</span>`,
      ...labels.map((l, i) => `<span><span class="swatch" style="background:${colors[i]}"></span>${l}</span>`),
      `<span><span class="swatch" style="background:${this.mutedColor}"></span>Unbudgeted / Unspent</span>`,
    ].join("");
  },

  /* ---- Budget adherence ---- */
  async renderAdherence() {
    const data = await apiGet(`/api/report/monthly_spending?month=${App.currentMonth}`);
    const income = await apiGet(`/api/income?month=${App.currentMonth}`);
    const netTakeHome = income.net_take_home || 0;

    const plannedSum = data.reduce((s, g) => s + g.planned, 0);
    const spentSum = data.reduce((s, g) => s + g.spent, 0);
    const overallPct = plannedSum > 0 ? (spentSum / plannedSum) * 100 : 0;

    const overallWrap = document.getElementById("adherence-overall");
    const barColor = overallPct > 100 ? "var(--negative)" : overallPct >= 90 ? "var(--warning)" : "var(--positive)";
    overallWrap.innerHTML = `
      <div class="adherence-top-row">
        <span class="big-pct">${overallPct.toFixed(0)}%</span>
        <div class="adherence-bar-track"><div class="adherence-bar-fill" style="width:0%; background:${barColor}" data-target-pct="${Math.min(100, overallPct)}"></div></div>
      </div>
      <p class="hint">of planned budget used so far (${formatCurrency(spentSum)} of ${formatCurrency(plannedSum)})</p>
    `;
    animateBarFills(overallWrap.querySelectorAll(".adherence-bar-fill"));

    const tbody = document.querySelector("#adherence-table tbody");
    const rows = data.map((g) => {
      const pct = g.planned > 0 ? (g.spent / g.planned) * 100 : (g.spent > 0 ? 100 : 0);
      const pctOfTakeHome = netTakeHome > 0 ? (g.spent / netTakeHome) * 100 : 0;
      let statusLabel = "On track";
      if (pct > 100) statusLabel = "Over";
      else if (pct >= 90) statusLabel = "Near limit";
      return { group: g.group, planned: g.planned, spent: g.spent, pct, pctOfTakeHome, statusLabel };
    });
    this.lastAdherenceRows = rows;

    tbody.innerHTML = rows.map((r) => {
      const statusClass = r.statusLabel === "Over" ? "over" : r.statusLabel === "Near limit" ? "near" : "under";
      return `
        <tr>
          <td>${r.group}</td>
          <td class="num">${formatCurrency(r.planned)}</td>
          <td class="num">${formatCurrency(r.spent)}</td>
          <td class="num">${r.pct.toFixed(0)}%</td>
          <td class="num">${r.pctOfTakeHome.toFixed(1)}%</td>
          <td><span class="status-pill ${statusClass}">${r.statusLabel}</span></td>
        </tr>
      `;
    }).join("") || `<tr><td colspan="6" class="hint">No budget groups for this month yet.</td></tr>`;
  },

  /* ---- Zero-based budget flow: hand-rolled SVG Sankey ---- */
  async renderBudgetFlow() {
    const flow = await apiGet(`/api/report/budget_flow?month=${App.currentMonth}`);
    const container = document.getElementById("budget-flow");
    this.drawSankey(flow, container);
  },

  drawSankey(flow, container) {
    const { nodes, links } = flow;
    if (!nodes.length || !links.length) {
      container.innerHTML = `<p class="hint">Add income and budget line items to see the flow.</p>`;
      return;
    }

    const width = Math.max(container.clientWidth || 900, 600);
    const height = 420;
    const nodeWidth = 14;
    const nodePadding = 18;
    const leftMargin = 10;
    const rightMargin = 190;

    const columns = [...new Set(nodes.map((n) => n.column))].sort((a, b) => a - b);
    const colX = {};
    columns.forEach((col, i) => {
      const span = width - leftMargin - rightMargin;
      colX[col] = leftMargin + (columns.length > 1 ? (i * span) / (columns.length - 1) : 0);
    });

    // Build node lookup with incoming/outgoing link references
    const nodeById = {};
    nodes.forEach((n) => { nodeById[n.id] = { ...n, in: [], out: [] }; });
    links.forEach((l) => {
      nodeById[l.source].out.push(l);
      nodeById[l.target].in.push(l);
    });
    Object.values(nodeById).forEach((n) => {
      const inSum = n.in.reduce((s, l) => s + l.value, 0);
      const outSum = n.out.reduce((s, l) => s + l.value, 0);
      n.value = Math.max(inSum, outSum, 0.01);
    });

    // Scale everything against whichever column is tightest on space: the
    // node heights (proportional to value) plus the padding *between* nodes
    // in that column must fit within usableHeight, or the column overflows
    // the SVG viewBox and gets clipped at the bottom.
    const usableHeight = height - 20;
    const scale = Math.min(
      ...columns.map((col) => {
        const colNodes = Object.values(nodeById).filter((n) => n.column === col);
        const colTotal = colNodes.reduce((s, n) => s + n.value, 0);
        const paddingNeeded = Math.max(colNodes.length - 1, 0) * nodePadding;
        return (usableHeight - paddingNeeded) / Math.max(colTotal, 0.01);
      })
    );

    // Stack nodes vertically within each column. Each node's bar height is
    // purely proportional to its value (min 3px so a tiny flow still shows
    // as a sliver), but a node's *label* needs real vertical room for its
    // two lines of text no matter how thin its bar is - labelSpan is a
    // separate floor used only for spacing/centering, so several small
    // nodes stacked together (e.g. an employer match alongside a second
    // pay schedule) don't get overlapping labels.
    const minLabelSpan = 26;
    columns.forEach((col) => {
      const colNodes = Object.values(nodeById).filter((n) => n.column === col);
      let y = 10;
      colNodes.forEach((n) => {
        n.x = colX[col];
        n.y = y;
        n.h = Math.max(n.value * scale, 3);
        n.labelSpan = Math.max(n.h, minLabelSpan);
        y += n.labelSpan + nodePadding;
      });
    });

    // Assign vertical slices on each node's edges for every link touching it
    Object.values(nodeById).forEach((n) => {
      let outOffset = n.y;
      n.out.forEach((l) => { l._sy = outOffset; l._sh = Math.max(l.value * scale, 1); outOffset += l._sh; });
      let inOffset = n.y;
      n.in.forEach((l) => { l._ty = inOffset; l._th = Math.max(l.value * scale, 1); inOffset += l._th; });
    });

    const lastCol = columns[columns.length - 1];
    let svg = `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img" aria-label="Zero-based budget Sankey diagram">`;

    // Links (drawn first, underneath the node bars)
    links.forEach((l, i) => {
      const s = nodeById[l.source];
      const t = nodeById[l.target];
      const x0 = s.x + nodeWidth;
      const x1 = t.x;
      const xm = (x0 + x1) / 2;
      const y0top = l._sy, y0bot = l._sy + l._sh;
      const y1top = l._ty, y1bot = l._ty + l._th;
      const path = `M${x0},${y0top}
                    C${xm},${y0top} ${xm},${y1top} ${x1},${y1top}
                    L${x1},${y1bot}
                    C${xm},${y1bot} ${xm},${y0bot} ${x0},${y0bot} Z`;
      const color = this.palette[i % this.palette.length];
      svg += `<path d="${path}" fill="${color}" opacity="0.45"><title>${s.label} \u2192 ${t.label}: ${formatCurrency(l.value)}</title></path>`;
    });

    // Nodes
    Object.values(nodeById).forEach((n) => {
      svg += `<rect x="${n.x}" y="${n.y}" width="${nodeWidth}" height="${n.h}" fill="#C7A15C" rx="2"><title>${n.label}: ${formatCurrency(n.value)}</title></rect>`;
      const labelX = n.x + nodeWidth + 8;
      svg += `<text x="${labelX}" y="${n.y + n.labelSpan / 2 - 6}" font-size="12" font-family="Inter, sans-serif" fill="#E9E4D8">${n.label}</text>`;
      svg += `<text x="${labelX}" y="${n.y + n.labelSpan / 2 + 9}" font-size="11" font-family="'IBM Plex Mono', monospace" fill="#93A0AF">${formatCurrency(n.value)}</text>`;
    });

    svg += `</svg>`;
    container.innerHTML = svg;
  },

  /* ---- Annual trend ---- */
  async renderAnnualTrend() {
    const year = document.getElementById("report-year").value || new Date().getFullYear();
    const rows = await apiGet(`/api/report/annual_trend?year=${year}`);

    const months = ["01","02","03","04","05","06","07","08","09","10","11","12"];
    const groupNames = [...new Set(rows.map((r) => r.group_name))];

    const datasets = groupNames.map((name, i) => ({
      label: name,
      data: months.map((m) => {
        const match = rows.find((r) => r.month_num === m && r.group_name === name);
        return match ? match.total : 0;
      }),
      borderColor: this.palette[i % this.palette.length],
      backgroundColor: "transparent",
      tension: 0.25,
    }));

    const ctx = document.getElementById("annual-trend-chart");
    if (this.trendChart) this.trendChart.destroy();
    this.trendChart = new Chart(ctx, {
      type: "line",
      data: { labels: months, datasets },
      options: {
        scales: {
          x: { ticks: { color: "#93A0AF" }, grid: { color: "#28323F" } },
          y: { ticks: { color: "#93A0AF" }, grid: { color: "#28323F" } },
        },
        plugins: { legend: { labels: { color: "#E9E4D8", font: { family: "Inter" } } } },
      },
    });
  },

  /* ---- Multi-Year Comparison ---- */
  async renderMultiYearComparison() {
    const anchorYear = parseInt(document.getElementById("report-year").value, 10) || new Date().getFullYear();
    const checklistWrap = document.getElementById("multi-year-checklist");

    if (!checklistWrap.dataset.built) {
      const years = [];
      for (let y = anchorYear - 4; y <= anchorYear; y++) years.push(y);
      checklistWrap.innerHTML = years.map((y) =>
        `<label class="checkbox-label"><input type="checkbox" class="multi-year-checkbox" value="${y}" ${y >= anchorYear - 1 ? "checked" : ""}/> ${y}</label>`
      ).join("");
      checklistWrap.dataset.built = "true";
      checklistWrap.querySelectorAll(".multi-year-checkbox").forEach((cb) => {
        cb.addEventListener("change", () => this.renderMultiYearChart());
      });
    }

    await this.renderMultiYearChart();
  },

  async renderMultiYearChart() {
    const checklistWrap = document.getElementById("multi-year-checklist");
    const selectedYears = [...checklistWrap.querySelectorAll(".multi-year-checkbox:checked")].map((cb) => cb.value);
    const ctx = document.getElementById("multi-year-chart");

    if (!selectedYears.length) {
      if (this.multiYearChart) { this.multiYearChart.destroy(); this.multiYearChart = null; }
      this.lastMultiYearData = null;
      return;
    }

    const data = await apiGet(`/api/report/multi_year_trend?years=${selectedYears.join(",")}`);
    this.lastMultiYearData = data;

    const monthLabels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const datasets = selectedYears.map((y, i) => ({
      label: y,
      data: data[y],
      borderColor: this.palette[i % this.palette.length],
      backgroundColor: "transparent",
      tension: 0.25,
      pointRadius: 0,
      borderWidth: 2,
    }));

    if (this.multiYearChart) this.multiYearChart.destroy();
    this.multiYearChart = new Chart(ctx, {
      type: "line",
      data: { labels: monthLabels, datasets },
      options: {
        scales: {
          x: { ticks: { color: "#93A0AF" }, grid: { color: "#28323F" } },
          y: { ticks: { color: "#93A0AF" }, grid: { color: "#28323F" } },
        },
        plugins: { legend: { labels: { color: "#E9E4D8", font: { family: "Inter" } } } },
      },
    });
  },

  /* ---- Savings Rate Over Time ---- */
  async renderSavingsRate() {
    const year = document.getElementById("report-year").value || new Date().getFullYear();

    const data = await apiGet(`/api/report/savings_rate?year=${year}`);
    this.lastSavingsRateData = data;

    const monthLabels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const rateSeries = data.map((row) => row.savings_rate_pct);

    // Loose, commonly-cited FI/budgeting benchmark tiers, just for a quick
    // visual read: red under 10%, gold 10-20%, green 20%+. Not a judgment
    // on any specific target -- just makes the chart scannable at a glance.
    const barColors = rateSeries.map((v) => {
      if (v === null) return this.mutedColor;
      if (v >= 20) return "#74A788";
      if (v >= 10) return "#C7A15C";
      return "#C3654D";
    });

    const ctx = document.getElementById("savings-rate-chart");
    if (this.savingsRateChart) this.savingsRateChart.destroy();
    this.savingsRateChart = new Chart(ctx, {
      type: "bar",
      data: {
        labels: monthLabels,
        datasets: [{ label: "Savings Rate", data: rateSeries, backgroundColor: barColors }],
      },
      options: {
        scales: {
          x: { ticks: { color: "#93A0AF" }, grid: { color: "#28323F" } },
          y: { ticks: { color: "#93A0AF", callback: (v) => `${v}%` }, grid: { color: "#28323F" } },
        },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (item) => item.raw === null ? "No income logged" : `${item.raw}% saved` } },
        },
      },
    });
  },

  /* ---- Year-End Tax Summary ---- */
  async renderTaxSummary() {
    const year = document.getElementById("report-year").value || new Date().getFullYear();

    const card = document.getElementById("tax-summary-card");
    const data = await apiGet(`/api/report/tax_summary?year=${year}`);
    if (!data) {
      card.style.display = "none";
      return;
    }
    card.style.display = "";

    const tbody = document.querySelector("#tax-summary-table tbody");
    tbody.innerHTML = data.sources.map((s) => `
      <tr>
        <td>${s.label}</td>
        <td class="num">${formatCurrency(s.gross)}</td>
        <td class="num">${formatCurrency(s.total_deductions)}</td>
        <td class="num">${formatCurrency(s.total_tax_withheld)}</td>
        <td class="num">${formatCurrency(s.total_investments)}</td>
        <td class="num">${formatCurrency(s.total_match)}</td>
        <td class="num">${formatCurrency(s.net)}</td>
      </tr>
    `).join("");

    const t = data.totals;
    document.querySelector("#tax-summary-table tfoot").innerHTML = `
      <tr>
        <td>Total</td>
        <td class="num">${formatCurrency(t.gross)}</td>
        <td class="num">${formatCurrency(t.total_deductions)}</td>
        <td class="num">${formatCurrency(t.total_tax_withheld)}</td>
        <td class="num">${formatCurrency(t.total_investments)}</td>
        <td class="num">${formatCurrency(t.total_match)}</td>
        <td class="num">${formatCurrency(t.net_take_home)}</td>
      </tr>
    `;

    this.renderTaxEstimate(data.estimate);
    this.bindFilingStatusControl();
  },

  /**
   * Renders the estimated-federal-tax block below the source table:
   * taxable income walk-through, the marginal bracket breakdown, and the
   * withheld-vs-estimated-liability comparison. `estimate` is the
   * `estimate` object returned alongside get_year_end_tax_summary.
   */
  renderTaxEstimate(estimate) {
    const select = document.getElementById("tax-filing-status");
    select.value = estimate.filing_status;

    const noteYear = estimate.bracket_year;
    const requestedYear = document.getElementById("report-year").value;
    const fallbackNote = String(noteYear) !== String(requestedYear)
      ? ` ${requestedYear} isn't in the app's bracket table yet, so the closest year on file (${noteYear}) was used instead.`
      : "";
    document.getElementById("tax-estimate-source-note").textContent =
      `Based on ${noteYear} IRS federal tax brackets and the ${noteYear} standard deduction for ${estimate.filing_status_label}.${fallbackNote} Estimate only - doesn't account for credits, itemizing, or income outside what's tracked here.`;

    const balance = estimate.estimated_balance;
    const balanceLabel = balance >= 0 ? "Estimated Refund" : "Estimated Amount Owed";
    const balanceClass = balance >= 0 ? "positive" : "negative";

    document.getElementById("tax-estimate-summary").innerHTML = `
      <div class="tax-estimate-stat">
        <div class="label">Taxable Income (est.)</div>
        <div class="value">${formatCurrency(estimate.taxable_income)}</div>
      </div>
      <div class="tax-estimate-stat">
        <div class="label">Marginal Bracket</div>
        <div class="value">${estimate.marginal_rate}%</div>
      </div>
      <div class="tax-estimate-stat">
        <div class="label">Effective Rate</div>
        <div class="value">${estimate.effective_rate}%</div>
      </div>
      <div class="tax-estimate-stat">
        <div class="label">Estimated Federal Tax</div>
        <div class="value">${formatCurrency(estimate.tax)}</div>
      </div>
      <div class="tax-estimate-stat">
        <div class="label">Already Withheld</div>
        <div class="value">${formatCurrency(estimate.amount_withheld)}</div>
      </div>
      <div class="tax-estimate-stat">
        <div class="label">${balanceLabel}</div>
        <div class="value ${balanceClass}">${formatCurrency(Math.abs(balance))}</div>
      </div>
    `;

    const tbody = document.querySelector("#tax-bracket-table tbody");
    tbody.innerHTML = estimate.breakdown.map((b) => `
      <tr>
        <td>${b.rate}%</td>
        <td>${formatCurrency(b.floor)} &ndash; ${b.ceiling ? formatCurrency(b.ceiling) : "and up"}</td>
        <td class="num">${formatCurrency(b.amount_taxed)}</td>
        <td class="num">${formatCurrency(b.tax)}</td>
      </tr>
    `).join("") || `<tr><td colspan="4" class="hint">No taxable income estimated for this year.</td></tr>`;
  },

  bindFilingStatusControl() {
    const select = document.getElementById("tax-filing-status");
    if (select.dataset.bound) return;
    select.dataset.bound = "true";
    select.addEventListener("change", async () => {
      try {
        await apiPut("/api/settings/tax_filing_status", { value: select.value });
      } catch (err) {
        showToast(`Couldn't save filing status: ${err.message}`, "error");
      }
      this.renderTaxSummary();
    });
  },

  /* ---- Export to Excel (full database) ---- */
  bindExportButtons() {
    const btn = document.getElementById("export-full-backup-btn");
    if (btn.dataset.bound) return;
    btn.dataset.bound = "true";
    btn.addEventListener("click", async () => {
      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Exporting...";
      try {
        const res = await fetch("/api/backup/export");
        if (!res.ok) throw new Error(`Export failed: ${res.status}`);
        const blob = await res.blob();
        const stamp = new Date().toISOString().slice(0, 10);
        await saveBlobAsFile(blob, `ledger_full_backup_${stamp}.xlsx`);
      } catch (err) {
        showToast(`Export failed: ${err.message}`, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = originalText;
      }
    });
  },

  /* ---- Net worth history (single year, contributed vs value) ---- */
  /**
   * Builds a small tiling canvas pattern of diagonal stripes, used as the
   * Contributed dataset's fill so it reads as "principal" rather than one
   * more colored account band - same visual language as the gray hatched
   * overlay on the Annual Report PDF's version of this chart.
   */
  diagonalStripePattern(strokeColor) {
    const size = 8;
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const c = canvas.getContext("2d");
    c.strokeStyle = strokeColor;
    c.lineWidth = 1.5;
    // Three parallel segments so the tile edges line up seamlessly when repeated.
    [[-2, 2, 2, -2], [0, size, size, 0], [size - 2, size + 2, size + 2, size - 2]].forEach(([x0, y0, x1, y1]) => {
      c.beginPath();
      c.moveTo(x0, y0);
      c.lineTo(x1, y1);
      c.stroke();
    });
    return c.createPattern(canvas, "repeat");
  },

  async renderNetWorthHistory() {
    const year = document.getElementById("report-year").value || new Date().getFullYear();

    const accounts = await apiGet("/api/report/net_worth_history");
    const dates = computeYearDates(accounts, year);

    const ctx = document.getElementById("net-worth-chart");
    if (!dates.length) {
      if (this.netWorthChart) { this.netWorthChart.destroy(); this.netWorthChart = null; }
      ctx.style.display = "none";
      let emptyMsg = document.getElementById("net-worth-chart-empty");
      if (!emptyMsg) {
        emptyMsg = document.createElement("p");
        emptyMsg.id = "net-worth-chart-empty";
        emptyMsg.className = "hint";
        ctx.after(emptyMsg);
      }
      emptyMsg.textContent = `No account activity in ${year}.`;
      return;
    }
    ctx.style.display = "";
    const emptyMsg = document.getElementById("net-worth-chart-empty");
    if (emptyMsg) emptyMsg.remove();

    const contributedSeries = dates.map((d) => {
      let total = 0;
      accounts.forEach((acc) => {
        acc.contributions.forEach((c) => { if (c.date <= d) total += c.amount; });
      });
      return total;
    });

    // Each account is its own stacked band (same palette as the Value Over
    // Time by Account chart / the PDF report) so the composition of the
    // total Market Value at any date is visible at a glance - e.g. a date
    // where Fund 1 is 25%, Fund 2 40%, Fund 3 5%, Fund 4 30% shows as four
    // correspondingly-sized colored bands there.
    const accountDatasets = accounts.map((acc, i) => ({
      label: acc.name,
      data: dates.map((d) => accountValueAt(acc, d)),
      borderColor: this.palette[i % this.palette.length],
      backgroundColor: this.palette[i % this.palette.length] + "B3", // ~70% opacity
      borderWidth: 1.5,
      pointRadius: 0,
      fill: true,
      stack: "accounts",
      order: 2,
      tension: 0.2,
    }));

    // Contributed is drawn on top of the stack (not part of it - its own
    // stack group keeps it from being summed into the account bands) with
    // a diagonal-stripe fill instead of a flat color. Chart.js draws
    // datasets with a LOWER 'order' value last, so giving this a lower
    // order than the account datasets above is what puts it visually on
    // top of them rather than underneath.
    const contributedDataset = {
      label: "Contributed",
      data: contributedSeries,
      borderColor: "#93A0AF",
      borderDash: [4, 4],
      borderWidth: 1.5,
      pointRadius: 0,
      backgroundColor: this.diagonalStripePattern("#93A0AF"),
      fill: "origin",
      stack: "contributed",
      order: 1,
      tension: 0.2,
    };

    if (this.netWorthChart) this.netWorthChart.destroy();
    this.netWorthChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: dates,
        datasets: [...accountDatasets, contributedDataset],
      },
      options: {
        scales: {
          x: { ticks: { color: "#93A0AF", autoSkip: false, callback: monthOnlyTickCallback(dates) }, grid: { color: "#28323F" } },
          y: { stacked: true, ticks: { color: "#93A0AF" }, grid: { color: "#28323F" } },
        },
        plugins: { legend: { labels: { color: "#E9E4D8", font: { family: "Inter" } } } },
      },
    });
  },

  /* ---- Investment Insights summary ---- */
  /**
   * Pulls the same per-account insights already computed for the Net
   * Worth Aggregator (Track tab) plus the blended portfolio view, and
   * shows them together here so a single Report visit surfaces "is this
   * actually a good investment" flags without switching tabs. Risk
   * Profile (Low/Medium/High) is still edited on the Track tab per
   * account -- this is a read-only summary.
   */
  async renderInvestmentInsights() {
    const card = document.getElementById("report-insights-card");
    const list = document.getElementById("report-insights-list");

    const [accounts, portfolio] = await Promise.all([
      apiGet("/api/report/net_worth_history"),
      apiGet("/api/portfolio/metrics"),
    ]);

    const accountsWithInsights = accounts.filter((a) => a.insights && a.insights.length);
    const portfolioInsights = (portfolio && portfolio.insights) || [];

    if (!accountsWithInsights.length && !portfolioInsights.length) {
      card.style.display = "none";
      return;
    }
    card.style.display = "";

    const sections = [];
    if (portfolioInsights.length) {
      sections.push(`
        <div class="insight-section">
          <h3 class="subtable-heading">Portfolio (blended across all accounts)</h3>
          ${renderInsightBadges(portfolioInsights)}
        </div>
      `);
    }
    accountsWithInsights.forEach((acc) => {
      sections.push(`
        <div class="insight-section">
          <h3 class="subtable-heading">${acc.name}</h3>
          ${renderInsightBadges(acc.insights)}
        </div>
      `);
    });

    list.innerHTML = sections.join("");
  },
};

/* =========================================================================
   Generate Annual Report modal - previews the server-built PDF inline,
   then saves it via PyWebView's native "Save As" dialog. A plain
   <a download> / blob-URL click has no browser download manager to catch
   it inside a chromeless native window, so that approach silently goes
   nowhere - the JS API bridge (window.pywebview.api.save_file) is the
   reliable way to get bytes onto disk from this kind of app.
   ========================================================================= */
document.addEventListener("DOMContentLoaded", () => {
  const modalBox = document.getElementById("annual-report-modal-box");
  const openBtn = document.getElementById("generate-annual-report-btn");
  const cancelBtn = document.getElementById("annual-report-cancel");
  const confirmBtn = document.getElementById("annual-report-confirm");
  const downloadBtn = document.getElementById("annual-report-download");
  const yearInput = document.getElementById("annual-report-year");
  const yearPicker = document.getElementById("annual-report-picker");
  const preview = document.getElementById("annual-report-preview");
  const statusEl = document.getElementById("annual-report-status");

  let currentBlob = null;
  let currentFilename = null;
  let currentPreviewUrl = null;

  function resetModal() {
    if (currentPreviewUrl) URL.revokeObjectURL(currentPreviewUrl);
    currentBlob = null;
    currentFilename = null;
    currentPreviewUrl = null;
    preview.style.display = "none";
    preview.src = "about:blank";
    yearPicker.style.display = "";
    confirmBtn.style.display = "";
    downloadBtn.style.display = "none";
    modalBox.classList.remove("wide");
    statusEl.textContent = "";
  }

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result.split(",")[1]);
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }

  // Cancel and backdrop-click used to each run their own copy of
  // "remove open class, then resetModal()" - both now just call
  // ModalManager.close(), which runs resetModal() once via onClose.
  ModalManager.register("annual-report-modal", { onClose: resetModal });

  openBtn.addEventListener("click", () => {
    yearInput.value = yearInput.value || new Date().getFullYear();
    resetModal();
    ModalManager.open("annual-report-modal");
  });

  cancelBtn.addEventListener("click", () => ModalManager.close("annual-report-modal"));

  confirmBtn.addEventListener("click", async () => {
    const year = yearInput.value || new Date().getFullYear();
    statusEl.textContent = "Building the PDF \u2014 this can take a few seconds for a full year...";
    confirmBtn.disabled = true;

    try {
      const res = await fetch(`/api/report/annual_pdf?year=${year}`);
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      currentBlob = await res.blob();
      currentFilename = `Annual_Report_${year}.pdf`;
      currentPreviewUrl = URL.createObjectURL(currentBlob);

      modalBox.classList.add("wide");
      yearPicker.style.display = "none";
      confirmBtn.style.display = "none";
      downloadBtn.style.display = "";
      preview.src = currentPreviewUrl;
      preview.style.display = "block";
      statusEl.textContent = "Preview ready \u2014 scroll through below, then download when you're happy with it.";
    } catch (err) {
      statusEl.textContent = `Failed to generate PDF: ${err.message}`;
    } finally {
      confirmBtn.disabled = false;
    }
  });

  downloadBtn.addEventListener("click", async () => {
    if (!currentBlob) return;
    downloadBtn.disabled = true;

    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.save_file) {
        // Native app: real "Save As" dialog + direct file write.
        const base64 = await blobToBase64(currentBlob);
        const result = await window.pywebview.api.save_file(base64, currentFilename);
        if (result && result.saved) {
          statusEl.textContent = `Saved to ${result.path}`;
        } else if (result && result.cancelled) {
          statusEl.textContent = "Save cancelled.";
        } else {
          statusEl.textContent = `Couldn't save the file${result && result.error ? `: ${result.error}` : "."}`;
        }
      } else {
        // Fallback for running in a regular browser during development.
        const url = URL.createObjectURL(currentBlob);
        const link = document.createElement("a");
        link.href = url;
        link.download = currentFilename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        statusEl.textContent = "Downloaded.";
      }
    } catch (err) {
      statusEl.textContent = `Couldn't save the file: ${err.message}`;
    } finally {
      downloadBtn.disabled = false;
    }
  });
});