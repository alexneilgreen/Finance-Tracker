/* =========================================================================
   report.js — Page 3: Report
   Uses Chart.js (loaded via CDN in index.html) for the donut and line
   charts. The donut is drawn as two concentric rings (budgeted / spent),
   each summing to the full Net Take-Home via an "Unbudgeted"/"Unspent"
   remainder slice. The "Zero-Based Budget Flow" is a hand-rolled SVG
   Sankey diagram — no extra CDN dependency, so it works the same
   whether or not the machine has internet access after first launch.
   ========================================================================= */

const Report = {
  donutChart: null,
  trendChart: null,
  netWorthChart: null,
  palette: ["#C7A15C", "#74A788", "#7A93B0", "#C3654D", "#B9A5D6", "#7FC1C6", "#D8B679", "#9FB3C8"],
  mutedColor: "#3A4552",

  async refresh() {
    document.getElementById("trend-year").value =
      document.getElementById("trend-year").value || new Date().getFullYear();

    await this.renderMonthlySpending();
    await this.renderAdherence();
    await this.renderBudgetFlow();
    await this.renderAnnualTrend();
    await this.renderNetWorthHistory();
    this.bindYearInput();
  },

  /* ---------------------- Monthly spending: two-ring donut ---------------------- */
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
              label: (item) => `${item.dataset.label}: ${item.label} — ${formatCurrency(item.raw)}`,
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

  /* ---------------------- Budget adherence ---------------------- */
  async renderAdherence() {
    const data = await apiGet(`/api/report/monthly_spending?month=${App.currentMonth}`);
    const plannedSum = data.reduce((s, g) => s + g.planned, 0);
    const spentSum = data.reduce((s, g) => s + g.spent, 0);
    const overallPct = plannedSum > 0 ? (spentSum / plannedSum) * 100 : 0;

    const overallWrap = document.getElementById("adherence-overall");
    const barColor = overallPct > 100 ? "var(--negative)" : overallPct >= 90 ? "var(--brass)" : "var(--positive)";
    overallWrap.innerHTML = `
      <span class="big-pct">${overallPct.toFixed(0)}%</span>
      <div class="adherence-bar-track"><div class="adherence-bar-fill" style="width:${Math.min(100, overallPct)}%; background:${barColor}"></div></div>
      <span class="hint">of planned budget used so far (${formatCurrency(spentSum)} of ${formatCurrency(plannedSum)})</span>
    `;

    const tbody = document.querySelector("#adherence-table tbody");
    tbody.innerHTML = data.map((g) => {
      const pct = g.planned > 0 ? (g.spent / g.planned) * 100 : (g.spent > 0 ? 100 : 0);
      let statusClass = "under", statusLabel = "On track";
      if (pct > 100) { statusClass = "over"; statusLabel = "Over"; }
      else if (pct >= 90) { statusClass = "near"; statusLabel = "Near limit"; }
      return `
        <tr>
          <td>${g.group}</td>
          <td class="num">${formatCurrency(g.planned)}</td>
          <td class="num">${formatCurrency(g.spent)}</td>
          <td class="num">${pct.toFixed(0)}%</td>
          <td><span class="status-pill ${statusClass}">${statusLabel}</span></td>
        </tr>
      `;
    }).join("") || `<tr><td colspan="5" class="hint">No budget groups for this month yet.</td></tr>`;
  },

  /* ---------------------- Zero-based budget flow: hand-rolled SVG Sankey ---------------------- */
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
    const nodePadding = 12;
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

    // Stack nodes vertically within each column
    columns.forEach((col) => {
      const colNodes = Object.values(nodeById).filter((n) => n.column === col);
      let y = 10;
      colNodes.forEach((n) => {
        n.x = colX[col];
        n.y = y;
        n.h = Math.max(n.value * scale, 3);
        y += n.h + nodePadding;
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
      svg += `<text x="${labelX}" y="${n.y + n.h / 2 - 6}" font-size="12" font-family="Inter, sans-serif" fill="#E9E4D8">${n.label}</text>`;
      svg += `<text x="${labelX}" y="${n.y + n.h / 2 + 9}" font-size="11" font-family="'IBM Plex Mono', monospace" fill="#93A0AF">${formatCurrency(n.value)}</text>`;
    });

    svg += `</svg>`;
    container.innerHTML = svg;
  },

  /* ---------------------- Annual trend ---------------------- */
  async renderAnnualTrend() {
    const year = document.getElementById("trend-year").value || new Date().getFullYear();
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

  bindYearInput() {
    const input = document.getElementById("trend-year");
    if (input.dataset.bound) return;
    input.dataset.bound = "true";
    input.addEventListener("change", () => this.renderAnnualTrend());
  },

  /* ---------------------- Net worth history ---------------------- */
  async renderNetWorthHistory() {
    const accounts = await apiGet("/api/report/net_worth_history");

    const allDates = new Set();
    accounts.forEach((acc) => {
      acc.contributions.forEach((c) => allDates.add(c.date));
      acc.valuations.forEach((v) => allDates.add(v.date));
    });
    const dates = [...allDates].sort();

    const contributedSeries = dates.map((d) => {
      let total = 0;
      accounts.forEach((acc) => {
        acc.contributions.forEach((c) => { if (c.date <= d) total += c.amount; });
      });
      return total;
    });

    const valueSeries = dates.map((d) => {
      let total = 0;
      accounts.forEach((acc) => {
        const applicable = acc.valuations.filter((v) => v.date <= d);
        if (applicable.length) {
          total += applicable[applicable.length - 1].value;
        } else {
          const contribs = acc.contributions.filter((c) => c.date <= d);
          total += contribs.reduce((s, c) => s + c.amount, 0);
        }
      });
      return total;
    });

    const ctx = document.getElementById("net-worth-chart");
    if (this.netWorthChart) this.netWorthChart.destroy();
    this.netWorthChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: dates,
        datasets: [
          { label: "Contributed", data: contributedSeries, borderColor: "#93A0AF", borderDash: [4, 4], backgroundColor: "transparent" },
          { label: "Market Value", data: valueSeries, borderColor: "#C7A15C", backgroundColor: "rgba(199,161,92,0.12)", fill: true, tension: 0.2 },
        ],
      },
      options: {
        scales: {
          x: { ticks: { color: "#93A0AF" }, grid: { color: "#28323F" } },
          y: { ticks: { color: "#93A0AF" }, grid: { color: "#28323F" } },
        },
        plugins: { legend: { labels: { color: "#E9E4D8", font: { family: "Inter" } } } },
      },
    });
  },
};

/* =========================================================================
   Generate Annual Report modal — previews the server-built PDF inline,
   then saves it via PyWebView's native "Save As" dialog. A plain
   <a download> / blob-URL click has no browser download manager to catch
   it inside a chromeless native window, so that approach silently goes
   nowhere — the JS API bridge (window.pywebview.api.save_file) is the
   reliable way to get bytes onto disk from this kind of app.
   ========================================================================= */
document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("annual-report-modal");
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

  openBtn.addEventListener("click", () => {
    yearInput.value = yearInput.value || new Date().getFullYear();
    resetModal();
    modal.classList.add("open");
  });

  cancelBtn.addEventListener("click", () => {
    modal.classList.remove("open");
    resetModal();
  });
  modal.addEventListener("click", (e) => {
    if (e.target === modal) {
      modal.classList.remove("open");
      resetModal();
    }
  });

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