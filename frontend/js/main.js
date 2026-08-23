/* =========================================================================
   main.js
   Core app logic: top-level nav routing, sub-nav routing (Track page),
   the global "Ledger Month" selector, and small shared helpers that
   budget.js / track.js / report.js all rely on.
   ========================================================================= */

const App = {
  currentMonth: null, // 'YYYY-MM', drives Budget + Track + top Report chart
};

/* ---------------------------------------------------------------------
   Shared helpers
   --------------------------------------------------------------------- */
async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`);
  return res.status === 204 ? null : res.json();
}

async function apiPut(path, body) {
  const res = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`PUT ${path} failed: ${res.status}`);
  return res.status === 204 ? null : res.json();
}

async function apiDelete(path) {
  const res = await fetch(path, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE ${path} failed: ${res.status}`);
}

function formatCurrency(n) {
  const val = Number(n) || 0;
  const sign = val < 0 ? "-" : "";
  return `${sign}$${Math.abs(val).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function currentMonthISO() {
  return new Date().toISOString().slice(0, 7);
}

/**
 * Animates progress-bar-style elements growing from 0 to their real width.
 * Elements must be rendered with style="width:0%" and a data-target-pct
 * attribute holding the real percentage; this flips them to that value one
 * frame later so the CSS width transition actually has something to animate
 * from (setting the final width immediately, in the same paint, produces no
 * visible transition at all).
 */
function animateBarFills(elements) {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      elements.forEach((el) => {
        el.style.width = `${el.dataset.targetPct}%`;
      });
    });
  });
}

/**
 * Builds the x-axis date list for a single-year net worth chart: every
 * contribution/valuation date that actually falls within `year`, plus a
 * synthetic Jan 1 point (if there's any data before the year started) so
 * the line starts from the correct carried-forward value instead of
 * jumping from zero.
 */
function computeYearDates(accounts, year) {
  const allDates = new Set();
  accounts.forEach((acc) => {
    acc.contributions.forEach((c) => allDates.add(c.date));
    acc.valuations.forEach((v) => allDates.add(v.date));
  });
  const sorted = [...allDates].sort();

  const yearStart = `${year}-01-01`;
  const yearEnd = `${year}-12-31`;
  const inYear = sorted.filter((d) => d >= yearStart && d <= yearEnd);
  const hasPriorData = sorted.some((d) => d < yearStart);

  const dates = [...inYear];
  if (hasPriorData && !dates.includes(yearStart)) {
    dates.unshift(yearStart);
  }
  return dates.sort();
}

/** Value of one account as of date `d`: latest valuation on/before d, or
 * the running contribution total if no valuation has been logged yet. */
function accountValueAt(account, d) {
  const applicable = account.valuations.filter((v) => v.date <= d);
  if (applicable.length) return applicable[applicable.length - 1].value;
  return account.contributions.filter((c) => c.date <= d).reduce((s, c) => s + c.amount, 0);
}

/**
 * Saves a Blob to disk. A plain <a download> / blob-URL click has no
 * browser download manager to catch it inside PyWebView's chromeless
 * window, so that approach silently goes nowhere there — the JS API
 * bridge (window.pywebview.api.save_file) is the reliable path. Falls
 * back to a normal browser download when running outside PyWebView
 * (e.g. during development in a regular browser tab).
 */
async function saveBlobAsFile(blob, filename) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.save_file) {
    const base64 = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result.split(",")[1]);
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
    return window.pywebview.api.save_file(base64, filename);
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
  return { saved: true, path: filename };
}

/**
 * Exports any report table to a real .xlsx via the backend's generic
 * pandas/openpyxl export endpoint — used for computed report tables
 * (Budget Adherence, Multi-Year Comparison, Savings Rate, Debt Payoff
 * Plan...) that don't exist as a single raw DB table the way transactions
 * or accounts do. `headers` is an array of column names; `rows` is an
 * array of arrays (or objects — plain values work fine as an object's
 * values array via Object.values if the caller prefers). `format` can be
 * "xlsx" (default) or "csv".
 */
async function exportRowsAsExcel(headers, rows, filenameBase, sheetName, format = "xlsx") {
  const res = await fetch("/api/export/generic", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ headers, rows, filename: filenameBase, sheet_name: sheetName || filenameBase, format }),
  });
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const blob = await res.blob();
  return saveBlobAsFile(blob, `${filenameBase}.${format === "csv" ? "csv" : "xlsx"}`);
}

/* ---------------------------------------------------------------------
   Top-level nav routing (Budget / Track / Report)
   --------------------------------------------------------------------- */
function initTopNav() {
  const tabs = document.querySelectorAll(".nav-tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");

      document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
      document.getElementById(`page-${tab.dataset.page}`).classList.add("active");

      // Refresh the newly-shown page's data
      if (tab.dataset.page === "budget") Budget.refresh();
      if (tab.dataset.page === "track") Track.refresh();
      if (tab.dataset.page === "report") Report.refresh();
    });
  });
}

/* ---------------------------------------------------------------------
   Sub-nav routing (Track page: Ledger / Sinking Funds / Net Worth)
   --------------------------------------------------------------------- */
function initSubNav() {
  const tabs = document.querySelectorAll(".subnav-tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");

      document.querySelectorAll(".subpage").forEach((p) => p.classList.remove("active"));
      document.getElementById(`subpage-${tab.dataset.subpage}`).classList.add("active");

      if (tab.dataset.subpage === "ledger") Track.refreshLedger();
      if (tab.dataset.subpage === "sinking") Track.refreshFunds();
      if (tab.dataset.subpage === "networth") Track.refreshAccounts();
      if (tab.dataset.subpage === "debts") Track.refreshDebts();
    });
  });
}

/* ---------------------------------------------------------------------
   Global month selector
   --------------------------------------------------------------------- */
function initGlobalMonth() {
  const input = document.getElementById("global-month");
  input.value = currentMonthISO();
  App.currentMonth = input.value;

  input.addEventListener("change", () => {
    App.currentMonth = input.value;
    Budget.refresh();
    Track.refresh();
  });
}

/**
 * Programmatically sets the global Ledger Month and refreshes the pages
 * that depend on it — the same effect as the person changing the picker
 * by hand. Used by the CSV-import month chips so a multi-month import can
 * jump straight to a given month's data instead of making the person
 * click through the picker themselves.
 */
function setGlobalMonth(month) {
  const input = document.getElementById("global-month");
  input.value = month;
  App.currentMonth = month;
  Budget.refresh();
  Track.refresh();
}

/* ---------------------------------------------------------------------
   Color scheme
   --------------------------------------------------------------------- */
async function initTheme() {
  const select = document.getElementById("theme-select");

  let theme = "dark-blue";
  try {
    const saved = await apiGet("/api/settings/theme");
    if (saved && saved.value) theme = saved.value;
  } catch (err) {
    // Fall back to the default theme if settings can't be reached.
  }

  document.documentElement.setAttribute("data-theme", theme);
  select.value = theme;

  select.addEventListener("change", async () => {
    const value = select.value;
    document.documentElement.setAttribute("data-theme", value);
    try {
      await apiPut("/api/settings/theme", { value });
    } catch (err) {
      // Theme still applies for this session even if saving the
      // preference fails; nothing further to do here.
    }
  });
}

/* ---------------------------------------------------------------------
   Collapsible cards (Pay Schedule, Log a transaction, Import Transactions,
   New sinking fund/goal, New account) — each starts minimized; its own
   toggle button only ever affects that one card's .card-content.
   --------------------------------------------------------------------- */
function initCollapsibleCards() {
  document.querySelectorAll("[data-card-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const card = btn.closest(".card");
      const collapsed = card.classList.toggle("collapsed");
      btn.textContent = collapsed ? "+" : "\u2212";
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    });
  });
}

/* ---------------------------------------------------------------------
   Import a full database backup (.xlsx from the Report page's Export to
   Excel) — destructive, so this requires an explicit confirmation before
   it touches anything, then reloads the whole app once it's done since
   virtually every page's data could have just changed underneath it.
   --------------------------------------------------------------------- */
function initImportBackup() {
  const btn = document.getElementById("import-backup-btn");
  const fileInput = document.getElementById("import-backup-file-input");

  btn.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (!file) return;

    const confirmed = confirm(
      "Importing a backup REPLACES existing data for every table found in the file. " +
      "This can't be undone. Are you sure you want to continue?"
    );
    if (!confirmed) {
      fileInput.value = "";
      return;
    }

    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Importing...";

    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch("/api/backup/import", { method: "POST", body: formData });
      const result = await res.json();
      if (!res.ok) throw new Error(result.error || `Server returned ${res.status}`);

      alert(`Import complete — restored ${result.tables_restored.length} table(s). Reloading the app now.`);
      window.location.reload();
    } catch (err) {
      alert(`Import failed: ${err.message}`);
      btn.disabled = false;
      btn.textContent = originalText;
    } finally {
      fileInput.value = "";
    }
  });
}

/* ---------------------------------------------------------------------
   Boot
   --------------------------------------------------------------------- */
document.addEventListener("DOMContentLoaded", () => {
  initTopNav();
  initSubNav();
  initGlobalMonth();
  initTheme();
  initCollapsibleCards();
  initImportBackup();

  Budget.refresh();
  Track.refresh();
  // Report page loads lazily when the tab is first opened
});