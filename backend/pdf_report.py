"""
pdf_report.py
-------------
Builds the multi-page "Annual Report" PDF: one page per month (spending
donut, budget adherence table, zero-based budget Sankey flow) followed by
a final page with the Annual Trend and Net Worth history charts.

Uses matplotlib's non-interactive Agg backend + PdfPages so the whole
document is assembled in memory (no temp files) and handed back as raw
PDF bytes for Flask to stream as a download.

The Sankey layout math here is a direct port of the client-side version
in frontend/js/report.js (same node-stacking + padding-aware scale fix),
so the PDF page matches what's on screen in the app.
"""

import io
import calendar

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.path import Path
from matplotlib.backends.backend_pdf import PdfPages

from backend import db_manager as db

# ---------------------------------------------------------------------------
# Print-friendly palette — light background, legible on paper, echoing the
# app's ink/brass ledger theme without dumping ink across a whole printed page.
# ---------------------------------------------------------------------------
PALETTE = ["#B08D3E", "#4C7A63", "#4C6E8C", "#A14B36", "#7C6592", "#3F8C8F", "#A9822E", "#5D7086"]
MUTED = "#C7CCD3"
TEXT_DARK = "#1C2530"
TEXT_MUTED = "#5B6572"
GRID = "#E3DFD2"
NODE_COLOR = "#B08D3E"


def _fmt(n):
    return f"${n:,.2f}"


def _draw_donut(ax, groups, net_take_home):
    ax.set_title("Monthly Spending", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    if net_take_home <= 0:
        ax.text(0.5, 0.5, "No income logged", ha="center", va="center", color=TEXT_MUTED, fontsize=9)
        ax.axis("off")
        return

    labels = [g["group"] for g in groups]
    budgeted = [g["planned"] for g in groups]
    spent = [g["spent"] for g in groups]
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(groups))]

    unbudgeted = max(net_take_home - sum(budgeted), 0)
    unspent = max(net_take_home - sum(spent), 0)
    budgeted_ring = budgeted + [unbudgeted]
    spent_ring = spent + [unspent]
    ring_colors = colors + [MUTED]

    # Outer ring = spent, inner ring = budgeted (same convention as the app).
    # Kept compact (radius <1) so this panel doesn't run noticeably taller
    # than the Budget Adherence table sharing this row.
    ax.pie(spent_ring, radius=0.85, colors=ring_colors,
           wedgeprops=dict(width=0.24, edgecolor="white", linewidth=1))
    ax.pie(budgeted_ring, radius=0.61, colors=ring_colors,
           wedgeprops=dict(width=0.24, edgecolor="white", linewidth=1))
    ax.text(0, 0, f"{_fmt(net_take_home)}\nNet Take-Home", ha="center", va="center",
            fontsize=7.5, color=TEXT_DARK)
    ax.axis("equal")

    handles = [patches.Patch(color=colors[i], label=labels[i]) for i in range(len(labels))]
    handles.append(patches.Patch(color=MUTED, label="Unbudgeted / Unspent"))
    if handles:
        ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.02),
                  fontsize=6.5, frameon=False, ncol=2)


def _draw_adherence_table(ax, groups):
    ax.set_title("Budget Adherence", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    ax.axis("off")
    if not groups:
        ax.text(0.5, 0.5, "No budget groups", ha="center", va="center", color=TEXT_MUTED, fontsize=9)
        return

    col_labels = ["Group", "Planned", "Spent", "% Used", "Status"]
    rows = []
    for g in groups:
        planned, spent = g["planned"], g["spent"]
        pct = (spent / planned * 100) if planned > 0 else (100 if spent > 0 else 0)
        status = "Over" if pct > 100 else ("Near limit" if pct >= 90 else "On track")
        rows.append([g["group"], _fmt(planned), _fmt(spent), f"{pct:.0f}%", status])

    # Stretched to fill roughly the same vertical footprint as the donut
    # chart it shares a row with, rather than a compact, mostly-empty box.
    # Capped so a preset with many groups can't overflow the axes.
    n_rows = len(rows) + 1
    height_frac = min(0.16 * n_rows, 0.92)
    y0 = max(1.0 - height_frac, 0.02)
    table = ax.table(cellText=rows, colLabels=col_labels, loc="upper left", cellLoc="left", colLoc="left",
                      bbox=[0.0, y0, 1.0, height_frac],
                      colWidths=[0.30, 0.18, 0.18, 0.15, 0.19])
    table.auto_set_font_size(False)
    table.set_fontsize(9.5)
    for (row, _col), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        if row == 0:
            cell.set_facecolor("#EDE7D8")
            cell.set_text_props(color=TEXT_DARK, weight="bold")
        else:
            cell.set_facecolor("white")
            cell.set_text_props(color=TEXT_DARK)


def _draw_sankey(ax, flow):
    ax.set_title("Zero-Based Budget Flow", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    ax.axis("off")
    nodes, links = flow["nodes"], flow["links"]
    if not nodes or not links:
        ax.text(0.5, 0.5, "Add income and budget line items to see the flow.",
                ha="center", va="center", color=TEXT_MUTED, fontsize=9)
        return

    width, height = 100.0, 55.0
    node_width, node_padding = 1.4, 1.8
    left_margin, right_margin = 2.0, 32.0

    columns = sorted({n["column"] for n in nodes})
    col_x = {}
    for i, col in enumerate(columns):
        span = width - left_margin - right_margin
        col_x[col] = left_margin + (i * span / (len(columns) - 1) if len(columns) > 1 else 0)

    node_by_id = {n["id"]: {**n, "in": [], "out": []} for n in nodes}
    for l in links:
        node_by_id[l["source"]]["out"].append(l)
        node_by_id[l["target"]]["in"].append(l)
    for n in node_by_id.values():
        in_sum = sum(l["value"] for l in n["in"])
        out_sum = sum(l["value"] for l in n["out"])
        n["value"] = max(in_sum, out_sum, 0.01)

    # Same padding-aware scale as the frontend: the tightest column (most
    # nodes relative to its total value) sets the scale so nothing overflows.
    usable_height = height - 4
    scale = min(
        (usable_height - max(len([n for n in node_by_id.values() if n["column"] == col]) - 1, 0) * node_padding)
        / max(sum(n["value"] for n in node_by_id.values() if n["column"] == col), 0.01)
        for col in columns
    )

    for col in columns:
        col_nodes = [n for n in node_by_id.values() if n["column"] == col]
        y = 2.0
        for n in col_nodes:
            n["x"] = col_x[col]
            n["y"] = y
            n["h"] = max(n["value"] * scale, 0.3)
            y += n["h"] + node_padding

    for n in node_by_id.values():
        out_offset = n["y"]
        for l in n["out"]:
            l["_sy"] = out_offset
            l["_sh"] = max(l["value"] * scale, 0.15)
            out_offset += l["_sh"]
        in_offset = n["y"]
        for l in n["in"]:
            l["_ty"] = in_offset
            l["_th"] = max(l["value"] * scale, 0.15)
            in_offset += l["_th"]

    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.invert_yaxis()

    for i, l in enumerate(links):
        s, t = node_by_id[l["source"]], node_by_id[l["target"]]
        x0, x1 = s["x"] + node_width, t["x"]
        xm = (x0 + x1) / 2
        y0top, y0bot = l["_sy"], l["_sy"] + l["_sh"]
        y1top, y1bot = l["_ty"], l["_ty"] + l["_th"]
        verts = [(x0, y0top), (xm, y0top), (xm, y1top), (x1, y1top),
                 (x1, y1bot), (xm, y1bot), (xm, y0bot), (x0, y0bot), (x0, y0top)]
        codes = [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4,
                 Path.LINETO, Path.CURVE4, Path.CURVE4, Path.CURVE4, Path.CLOSEPOLY]
        ax.add_patch(patches.PathPatch(Path(verts, codes), facecolor=PALETTE[i % len(PALETTE)],
                                        edgecolor="none", alpha=0.45))

    for n in node_by_id.values():
        ax.add_patch(patches.Rectangle((n["x"], n["y"]), node_width, n["h"],
                                        facecolor=NODE_COLOR, edgecolor="none"))
        label_x = n["x"] + node_width + 0.6
        ax.text(label_x, n["y"] + n["h"] / 2 - 1.0, n["label"], fontsize=6.5, color=TEXT_DARK, va="center")
        ax.text(label_x, n["y"] + n["h"] / 2 + 1.3, _fmt(n["value"]), fontsize=6, color=TEXT_MUTED, va="center")


def _build_month_page(pdf, year, month):
    month_str = f"{year}-{month:02d}"
    month_name = calendar.month_name[month]

    income = db.get_income_summary(month_str)
    groups = db.get_monthly_spending_report(month_str)
    flow = db.get_budget_flow(month_str)

    fig = plt.figure(figsize=(11, 8.5))
    fig.patch.set_facecolor("white")
    fig.suptitle(f"{month_name} {year}", fontsize=18, color=TEXT_DARK, family="serif", y=0.97)

    gs = fig.add_gridspec(2, 2, height_ratios=[1.1, 1], hspace=0.55, wspace=0.35,
                           left=0.06, right=0.96, top=0.88, bottom=0.06)
    ax_donut = fig.add_subplot(gs[0, 0])
    ax_table = fig.add_subplot(gs[0, 1])
    ax_sankey = fig.add_subplot(gs[1, :])

    _draw_donut(ax_donut, groups, income["net_take_home"])
    _draw_adherence_table(ax_table, groups)

    if income["gross"] > 0:
        _draw_sankey(ax_sankey, flow)
    else:
        ax_sankey.set_title("Zero-Based Budget Flow", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
        ax_sankey.axis("off")
        ax_sankey.text(0.5, 0.5, "No income logged for this month.",
                        ha="center", va="center", color=TEXT_MUTED, fontsize=9)

    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def _build_annual_charts_page(pdf, year):
    rows = db.get_annual_trend(year)
    months = [f"{m:02d}" for m in range(1, 13)]
    month_labels = [calendar.month_abbr[m] for m in range(1, 13)]
    group_names = sorted({r["group_name"] for r in rows})

    accounts = db.get_net_worth_history()
    all_dates = sorted(
        {c["date"] for a in accounts for c in a["contributions"]}
        | {v["date"] for a in accounts for v in a["valuations"]}
    )

    fig, (ax_trend, ax_net_worth) = plt.subplots(2, 1, figsize=(11, 8.5))
    fig.patch.set_facecolor("white")
    fig.suptitle(f"{year} \u2014 Annual Trend & Net Worth", fontsize=18, color=TEXT_DARK, family="serif", y=0.97)
    fig.subplots_adjust(left=0.08, right=0.96, top=0.88, bottom=0.1, hspace=0.5)

    ax_trend.set_title("Annual Trend", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    if group_names:
        for i, name in enumerate(group_names):
            series = []
            for m in months:
                match = next((r for r in rows if r["month_num"] == m and r["group_name"] == name), None)
                series.append(match["total"] if match else 0)
            ax_trend.plot(month_labels, series, label=name, color=PALETTE[i % len(PALETTE)], linewidth=1.8)
        ax_trend.legend(fontsize=7, frameon=False, ncol=4)
    else:
        ax_trend.text(0.5, 0.5, "No spending recorded this year", ha="center", va="center", color=TEXT_MUTED)
    ax_trend.set_facecolor("white")
    ax_trend.grid(color=GRID, linewidth=0.6)
    ax_trend.tick_params(colors=TEXT_MUTED, labelsize=7)
    for spine in ax_trend.spines.values():
        spine.set_color(GRID)

    ax_net_worth.set_title("Net Worth: Contributions vs. Market Value", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    if all_dates:
        contributed_series, value_series = [], []
        for d in all_dates:
            total_contrib = sum(c["amount"] for a in accounts for c in a["contributions"] if c["date"] <= d)
            total_value = 0.0
            for a in accounts:
                applicable = [v for v in a["valuations"] if v["date"] <= d]
                if applicable:
                    total_value += applicable[-1]["value"]
                else:
                    total_value += sum(c["amount"] for c in a["contributions"] if c["date"] <= d)
            contributed_series.append(total_contrib)
            value_series.append(total_value)
        ax_net_worth.plot(all_dates, contributed_series, label="Contributed", color=TEXT_MUTED, linestyle="--", linewidth=1.5)
        ax_net_worth.plot(all_dates, value_series, label="Market Value", color=PALETTE[0], linewidth=1.8)
        ax_net_worth.fill_between(all_dates, value_series, color=PALETTE[0], alpha=0.08)
        ax_net_worth.legend(fontsize=7, frameon=False)
        ax_net_worth.tick_params(axis="x", rotation=45, labelsize=6.5)
    else:
        ax_net_worth.text(0.5, 0.5, "No account history logged yet", ha="center", va="center", color=TEXT_MUTED)
    ax_net_worth.set_facecolor("white")
    ax_net_worth.grid(color=GRID, linewidth=0.6)
    ax_net_worth.tick_params(colors=TEXT_MUTED, labelsize=7)
    for spine in ax_net_worth.spines.values():
        spine.set_color(GRID)

    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def generate_annual_report(year):
    """Returns a BytesIO containing the full multi-page PDF for the given year."""
    year = int(year)
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        for month in range(1, 13):
            _build_month_page(pdf, year, month)
        _build_annual_charts_page(pdf, year)

        info = pdf.infodict()
        info["Title"] = f"Personal Finance Annual Report {year}"
        info["Author"] = "Ledger"

    buf.seek(0)
    return buf