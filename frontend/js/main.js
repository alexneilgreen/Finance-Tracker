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

/**
 * Value of one account as of date `d`: latest valuation on/before d, or
 * the running contribution total if no valuation has been logged yet.
 */
function accountValueAt(account, d) {
  const applicable = account.valuations.filter((v) => v.date <= d);
  if (applicable.length) return applicable[applicable.length - 1].value;
  return account.contributions.filter((c) => c.date <= d).reduce((s, c) => s + c.amount, 0);
}

/**
 * Saves a Blob to disk. A plain <a download> / blob-URL click has no
 * browser download manager to catch it inside PyWebView's chromeless
 * window, so that approach silently goes nowhere there - the JS API
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
 * pandas/openpyxl export endpoint - used for computed report tables
 * (Budget Adherence, Multi-Year Comparison, Savings Rate, Debt Payoff
 * Plan...) that don't exist as a single raw DB table the way transactions
 * or accounts do. `headers` is an array of column names; `rows` is an
 * array of arrays (or objects - plain values work fine as an object's
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

/**
 * Renders a list of investment insights (see generate_investment_insights()
 * in db_manager.py) as color-coded badges -- warnings first, then
 * favorable/good flags, then neutral notes like "not enough history yet."
 * Shared by Track's per-account/portfolio cards and Report's summary card
 * so both surfaces render the exact same markup.
 */
function renderInsightBadges(insights) {
  if (!insights || !insights.length) return "";
  const severityColor = { good: "var(--positive)", warning: "var(--negative)", neutral: "var(--text-muted)" };
  return `
    <div class="insight-list">
      ${insights.map((ins) => `
        <div class="insight-badge insight-${ins.severity}" style="border-left-color:${severityColor[ins.severity] || "var(--text-muted)"}">
          <div class="insight-label" style="color:${severityColor[ins.severity] || "var(--text-muted)"}">${ins.label}</div>
          <div class="insight-detail">${ins.detail}</div>
        </div>
      `).join("")}
    </div>
  `;
}

/**
 * Shows a floating, auto-dismissing toast in the top-right corner - the
 * replacement for native alert() throughout the app. `type` controls the
 * left accent color: "info" (default/accent gold), "success" (green),
 * "warning" (amber), or "error" (red). Click a toast to dismiss it early;
 * otherwise it fades out and removes itself after `duration` ms.
 */
function showToast(message, type = "info", duration = 5000) {
  const stack = document.getElementById("toast-stack");
  if (!stack) return; // Shouldn't happen, but never let a toast failure break the caller's flow.

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;

  let dismissed = false;
  const dismiss = () => {
    if (dismissed) return;
    dismissed = true;
    toast.classList.remove("toast-visible");
    toast.addEventListener("transitionend", () => toast.remove(), { once: true });
    // Belt-and-suspenders in case transitionend never fires (e.g. element
    // removed from the DOM some other way first).
    setTimeout(() => toast.remove(), 400);
  };
  toast.addEventListener("click", dismiss);

  stack.appendChild(toast);
  // Same double-rAF trick as animateBarFills - forces the "enter" transition
  // to actually animate instead of snapping straight to its end state.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => toast.classList.add("toast-visible"));
  });
  setTimeout(dismiss, duration);
}

/**
 * Consolidates the modal open/close, backdrop-click, and Escape-key
 * handling that used to be copy-pasted per modal (save-preset in
 * budget.js, split-transaction in track.js, annual-report in report.js -
 * all three had their own `close = () => modal.classList.remove("open")`
 * plus an identical `modal.addEventListener("click", (e) => { if
 * (e.target === modal) close(); })`). Each modal still owns its own
 * markup and button wiring; this only owns the open/close mechanics.
 *
 * Usage:
 *   ModalManager.register("save-preset-modal", {
 *     onOpen:  () => { ...reset fields... },
 *     onClose: () => { ...teardown, e.g. revoke an object URL... },
 *   });
 *   ModalManager.open("save-preset-modal");
 *   ModalManager.close("save-preset-modal");
 *
 * register() is idempotent (like the data.bound guard pattern used for
 * event listeners elsewhere), so calling it more than once for the same
 * id is safe and only wires the backdrop-click listener once.
 *
 * New behavior gained by centralizing this: Escape now closes whichever
 * registered modal is currently open. No modal in the app had this
 * before - it isn't a like-for-like port, it's a small added capability
 * that came for free from having one place that knows which modals exist.
 */
const ModalManager = {
  _registry: new Map(),
  _escBound: false,

  register(id, { onOpen, onClose } = {}) {
    const modal = document.getElementById(id);
    if (!modal || this._registry.has(id)) return;
    this._registry.set(id, { onOpen, onClose });

    modal.addEventListener("click", (e) => {
      if (e.target === modal) this.close(id);
    });

    if (!this._escBound) {
      this._escBound = true;
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") this._closeWhicheverIsOpen();
      });
    }
  },

  open(id) {
    const modal = document.getElementById(id);
    const entry = this._registry.get(id);
    if (!modal || !entry) return;
    if (entry.onOpen) entry.onOpen();
    modal.classList.add("open");
  },

  close(id) {
    const modal = document.getElementById(id);
    const entry = this._registry.get(id);
    if (!modal || !entry) return;
    modal.classList.remove("open");
    if (entry.onClose) entry.onClose();
  },

  // Only one modal is ever open at a time in this app, so Escape just
  // needs to find it and route through close() so onClose still runs
  // (the annual-report modal relies on onClose for its iframe/URL teardown).
  _closeWhicheverIsOpen() {
    for (const id of this._registry.keys()) {
      const modal = document.getElementById(id);
      if (modal && modal.classList.contains("open")) {
        this.close(id);
        return;
      }
    }
  },
};

/**
 * Chart.js x-axis tick callback for a date-labeled line/area chart:
 * labels only the first data point of each calendar month (blank string
 * for every other point in that month), so the axis reads as one label
 * per month no matter how many points land within a given month. Doesn't
 * touch the data itself - every point is still plotted and still drives
 * its own gridline/hover target, only the tick *text* is thinned.
 * `dates` is the same array passed as the chart's `data.labels`.
 */
function monthOnlyTickCallback(dates) {
  const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return function (_value, index) {
    const date = dates[index];
    if (!date) return "";
    const isFirstOfMonth = index === 0 || date.slice(0, 7) !== dates[index - 1].slice(0, 7);
    if (!isFirstOfMonth) return "";
    const [year, month] = date.split("-");
    return `${monthNames[parseInt(month, 10) - 1]} ${year}`;
  };
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

/**
 * Adds `delta` whole months to a 'YYYY-MM' string, correctly rolling over
 * year boundaries in either direction (e.g. 2025-01 + (-1) -> 2024-12).
 * Anchored to the 1st of the month so JS Date's own month-overflow
 * normalization does the rollover math instead of hand-rolled modulo logic.
 */
function shiftMonth(month, delta) {
  const [year, mo] = month.split("-").map(Number);
  const d = new Date(year, mo - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
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
    Report.refresh();
  });

  document.getElementById("global-month-prev").addEventListener("click", () => {
    setGlobalMonth(shiftMonth(App.currentMonth, -1));
  });
  document.getElementById("global-month-next").addEventListener("click", () => {
    setGlobalMonth(shiftMonth(App.currentMonth, 1));
  });
}

/**
 * Programmatically sets the global Ledger Month and refreshes the pages
 * that depend on it - the same effect as the person changing the picker
 * by hand. Used by the CSV-import month chips (and the Prev/Next buttons)
 * so jumping to a given month doesn't require going through the native
 * month-picker UI.
 */
function setGlobalMonth(month) {
  const input = document.getElementById("global-month");
  input.value = month;
  App.currentMonth = month;
  Budget.refresh();
  Track.refresh();
  Report.refresh();
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
   Collapsible cards (Pay Schedule, Transactions, New sinking fund/goal,
   New account, New debt, Recent Transactions...) - each card's open/closed
   state is remembered per-card via the generic /api/settings/<key> store
   (same mechanism as the color theme), so a card you left open stays open
   next session instead of re-collapsing every single time. On top of that
   remembered state, Budget/Track call autoExpandIfEmpty() once their data
   has loaded to force a card open when it's empty or otherwise needs
   attention (e.g. no pay schedule configured yet) - that override never
   overwrites the saved preference, and never forces a card *closed*.
   --------------------------------------------------------------------- */
function setCardCollapsed(card, btn, collapsed) {
  card.classList.toggle("collapsed", collapsed);
  btn.textContent = collapsed ? "+" : "\u2212";
  btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
}

async function initCollapsibleCards() {
  const toggles = [...document.querySelectorAll("[data-card-toggle]")];
  await Promise.all(toggles.map(async (btn) => {
    const card = btn.closest(".card");
    if (!card.id) return; // No stable key to persist against - leave its markup default alone.

    let collapsed = card.classList.contains("collapsed");
    try {
      const saved = await apiGet(`/api/settings/card_collapsed_${card.id}`);
      if (saved && saved.value != null) collapsed = saved.value === "true";
    } catch (err) {
      // No saved preference yet (or settings unreachable) - keep the markup's default.
    }
    setCardCollapsed(card, btn, collapsed);

    btn.addEventListener("click", async () => {
      const nowCollapsed = card.classList.toggle("collapsed");
      btn.textContent = nowCollapsed ? "+" : "\u2212";
      btn.setAttribute("aria-expanded", nowCollapsed ? "false" : "true");
      try {
        await apiPut(`/api/settings/card_collapsed_${card.id}`, { value: String(nowCollapsed) });
      } catch (err) {
        // Toggle still works for this session even if saving the preference fails.
      }
    });
  }));
}

/**
 * Forces a collapsed card open when `needsAttention` is true (empty list,
 * nothing configured yet, etc.) - called by Budget/Track after their data
 * loads. Deliberately one-directional: never collapses a card the person
 * left open, and never persists the override as if it were a real choice.
 */
function autoExpandIfEmpty(cardId, needsAttention) {
  if (!needsAttention) return;
  const card = document.getElementById(cardId);
  if (!card || !card.classList.contains("collapsed")) return;
  const btn = card.querySelector("[data-card-toggle]");
  if (!btn) return;
  setCardCollapsed(card, btn, false);
}

/* ---------------------------------------------------------------------
   Import a full database backup (.xlsx from the Report page's Export to
   Excel) - destructive, so this requires an explicit confirmation before
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

      showToast(`Import complete - restored ${result.tables_restored.length} table(s). Reloading the app now.`, "success");
      setTimeout(() => window.location.reload(), 1200); // Give the toast a moment to actually be seen before reload.
    } catch (err) {
      showToast(`Import failed: ${err.message}`, "error");
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
document.addEventListener("DOMContentLoaded", async () => {
  initTopNav();
  initSubNav();
  initGlobalMonth();
  initTheme();
  await initCollapsibleCards(); // Awaited so Budget/Track's autoExpandIfEmpty() calls right after can't race the saved-state fetch above.
  initImportBackup();

  Budget.refresh();
  Track.refresh();
  // Report page loads lazily when the tab is first opened
});