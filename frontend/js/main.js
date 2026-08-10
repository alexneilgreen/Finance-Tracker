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

/* ---------------------------------------------------------------------
   Boot
   --------------------------------------------------------------------- */
document.addEventListener("DOMContentLoaded", () => {
  initTopNav();
  initSubNav();
  initGlobalMonth();

  Budget.refresh();
  Track.refresh();
  // Report page loads lazily when the tab is first opened
});
