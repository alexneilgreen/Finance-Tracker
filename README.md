# Ledger — Personal Finance & Investment Tracker

Local-only desktop finance app. Python + SQLite backend, single-page
HTML/CSS/JS frontend, rendered in a native window via PyWebView.

## 1. Setup (one time)

```bash
cd finance_tracker
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Run it

```bash
python main.py
```

This starts a Flask server bound to `127.0.0.1` on a free local port and
opens it in a native window. The first run creates `data/finance.db`
automatically — nothing to configure.

## 3. Everyday use

- **Budget** tab: log income, deductions, and pre-tax investments to see
  Net Take-Home, then assign it to groups and line items.
- **Track** tab: log daily transactions against those line items, manage
  sinking funds/goals, and log account contributions + valuations.
- **Report** tab: spending donut + breakdown table, the zero-based budget
  flow, annual trend, and net worth (contributions vs. market value).
- Use `/api/export/<table>?format=xlsx` (or `csv`) in a browser tab while
  the app is running to pull any table into Excel — e.g.
  `http://127.0.0.1:<port>/api/export/transactions?format=xlsx`. The port
  is printed nowhere by design (security), so add a "Download" button
  wired to that route if you want one-click exports from the UI.
- Data lives entirely in `data/finance.db`. Closing the window copies a
  timestamped backup into `data/backups/` (last 20 kept automatically).

## 4. Package as a single executable (optional)

```bash
pyinstaller --noconfirm --onefile --add-data "frontend:frontend" main.py
```

The compiled app appears in `dist/`. Copy the `data/` folder alongside it
on first run if you want to carry over existing entries.

## Notes on the blueprint's design choices

- The frontend loads Chart.js from a CDN for the donut and trend charts,
  which requires internet access the first time (browsers cache it after
  that). If you need the app fully offline from first launch, download
  `chart.umd.min.js` and reference it locally instead of the CDN link in
  `frontend/index.html`.
- The "Zero-Based Budget Flow" on the Report page is a lightweight,
  hand-built horizontal flow view rather than a full Sankey chart library,
  to keep the dependency list minimal — swap in a Sankey plugin later if
  you want the literal diagram style.
