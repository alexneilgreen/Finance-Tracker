<!-- SHOWCASE: true -->

# Ledger

> A fully local desktop application for tracking personal finance, zero-based budgets, and net worth.

![Status](https://img.shields.io/badge/status-in_progress-yellow)
![Language](https://img.shields.io/badge/language-Python-blue)
![Timeline](https://img.shields.io/badge/timeline-Summer%202026-orange)

---

## Project Description

Ledger is a personal finance and investment tracker designed as a local desktop application. It provides tools for zero-based budgeting, tracking daily transactions, managing sinking funds, and aggregating net worth across various accounts. The application utilizes a Python and SQLite backend paired with a single-page HTML and JavaScript frontend, all rendered in a native window using PyWebView. I built this out of personal interest to create a custom tool capable of handling detailed zero-based budgeting while accurately tracking pre-tax retirement vehicles and long-term investments.

---

## Screenshots / Demo

> _No screenshot available. Add one with: `![Demo](docs/your-image.png)`_

---

## Results

When run correctly, the program launches a standalone desktop application window containing the Ledger interface[cite: 5]. The terminal running in the background will display standard Flask server logs on a local port, ensuring no network exposure[cite: 5]. 

```
 * Serving Flask app 'api'
 * Debug mode: off
 * Running on http://127.0.0.1:xxxxx
```

Users navigate between three main tabs: Budget, Track, and Report[cite: 6]. Within the Report tab, users can generate a multi-page Annual Report PDF[cite: 3]. This generated file contains monthly spending donut charts, budget adherence tables, and Sankey diagrams visualizing the flow of gross income[cite: 3]. Users can also export their database tables directly to `.xlsx` or `.csv` files by accessing the API export routes[cite: 1]. If the interface fails to load, check the terminal for port binding issues or missing dependencies. Data is automatically backed up to a local folder upon closing the application[cite: 5].

---

## Key Concepts

`Zero-Based Budgeting` `Single-Page Application (SPA)` `Local API Server` `Desktop Web Wrapper` `Relational Database`

---

## Languages & Tools

- **Language:** Python, JavaScript, HTML, CSS
- **Framework/SDK:** Flask, PyWebView, Matplotlib, Pandas, Chart.js
- **Build System:** PyInstaller

---

## File Structure

```
finance_tracker/
├── main.py                 # Entry point that launches PyWebView and the local server[cite: 5]
├── backend/
│   ├── api.py              # Local API handling HTTP requests and database routing[cite: 1]
│   ├── db_manager.py       # SQLite database schema, connection, and queries[cite: 2]
│   └── pdf_report.py       # Multi-page PDF annual report generator using Matplotlib[cite: 3]
├── frontend/
│   ├── index.html          # SPA containing all page containers and UI layout[cite: 6]
│   ├── css/
│   │   └── style.css       # Desktop-maximized styling and visual theme[cite: 7]
│   └── js/
│       ├── main.js         # Core logic, main navbar routing, and shared helpers[cite: 9]
│       ├── budget.js       # Template building and income parsing logic[cite: 8]
│       ├── track.js        # Daily ledger, sinking funds, and net worth tracking[cite: 11]
│       └── report.js       # Chart generation, budget flow SVG, and PDF preview[cite: 10]
└── data/
    ├── finance.db          # Local SQLite database file[cite: 2, 5]
    └── backups/            # Automated database backups saved on application close[cite: 5]
```

---

## Installation & Usage

### Prerequisites
- Python 3.8+

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/ledger.git
cd ledger

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the application
python main.py
```

### Controls

| Interface Element | Action |
|-------------------|--------|
| Main Navbar | Switches between Budget, Track, and Report modules. |
| Global Month Input | Changes the active month for budgeting and tracking. |
| Generate Annual Report Button | Builds a PDF of all financial history for a selected year. |

---

## License

> _No license specified. All rights reserved by the author._