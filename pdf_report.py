"""
PDF report generator — builds a client-style PDF (blue stat cards, black
section bars, trend charts, and full data tables) from the same GSC/GA4
data the dashboard uses.
"""

import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

import gsc_client
import ga4_client
import serper_client

BLUE = colors.HexColor("#2f8fd6")
DARK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#6b7280")
ROW_ALT = colors.HexColor("#f2f4f7")
GREEN = colors.HexColor("#33c17a")
RED = colors.HexColor("#d84343")

# Matches the dashboard's own light-theme card look (white card, thin border,
# muted uppercase label, bold dark value) instead of a flat solid-color box.
CARD_BG = colors.HexColor("#ffffff")
CARD_BORDER = colors.HexColor("#dfe2e7")
CARD_TEXT = colors.HexColor("#1a1d23")
CARD_MUTED = colors.HexColor("#667085")
CARD_ACCENT = colors.HexColor("#2f6fed")
CARD_TEXT_HEX = "#1a1d23"
CARD_MUTED_HEX = "#667085"

_styles = getSampleStyleSheet()
_cell_style = ParagraphStyle("cell", parent=_styles["Normal"], fontSize=8, leading=10)
_head_style = ParagraphStyle("head", parent=_styles["Normal"], fontSize=8, leading=10,
                              textColor=colors.white)


def _band(text):
    style = ParagraphStyle("band", parent=_styles["Normal"], textColor=colors.white,
                            backColor=DARK, fontSize=11, leading=18, spaceBefore=4, spaceAfter=10,
                            leftIndent=8, borderPadding=6)
    return Paragraph(text.upper(), style)


def _sub_title(text):
    style = ParagraphStyle("subtitle", parent=_styles["Normal"], fontSize=10, leading=14,
                            spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#2b2f36"))
    return Paragraph(f"<b>{text}</b>", style)


def _chunk(pairs, size=4):
    for i in range(0, len(pairs), size):
        yield pairs[i:i + size]


def _stat_table(pairs):
    """A row of separate white 'cards' — thin border, colored top accent,
    muted uppercase label + bold dark value — matching the dashboard's own
    .stat-card look, instead of one continuous solid-color block."""
    n = len(pairs)
    gap = 3 * mm
    total_width = 170 * mm
    card_width = (total_width - gap * (n - 1)) / n if n > 1 else total_width

    row = []
    col_widths = []
    for i, (label, value) in enumerate(pairs):
        row.append(Paragraph(
            f"<font size=7 color='{CARD_MUTED_HEX}'>{label.upper()}</font><br/>"
            f"<font size=15 color='{CARD_TEXT_HEX}'><b>{value}</b></font>",
            _styles["Normal"],
        ))
        col_widths.append(card_width)
        if i != n - 1:
            row.append("")  # spacer cell — no border applied, keeps cards visually separate
            col_widths.append(gap)

    t = Table([row], colWidths=col_widths)
    style = [
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for i in range(n):
        col = i * 2  # every other column is a real card; odd columns are spacers
        style.append(("BACKGROUND", (col, 0), (col, 0), CARD_BG))
        style.append(("BOX", (col, 0), (col, 0), 0.75, CARD_BORDER))
        style.append(("LINEABOVE", (col, 0), (col, 0), 2.2, CARD_ACCENT))
    t.setStyle(TableStyle(style))
    return t


def _line_chart_image(x_labels, series, labels, hex_colors):
    fig, ax = plt.subplots(figsize=(6.7, 2.3), dpi=150)
    for vals, label, c in zip(series, labels, hex_colors):
        ax.plot(x_labels, vals, label=label, color=c, linewidth=1.5)
    ax.legend(fontsize=7, frameon=False, loc="upper left")
    ax.tick_params(labelsize=6, rotation=45)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


def _donut_image(labels, values, hex_colors):
    """Renders percentages inside larger wedges only, and puts every slice's
    name + share in a legend to the right instead of labelling wedges
    directly — avoids the label pile-up that happens when several slices
    are small (e.g. Referral, Unassigned, Organic Social all under 5%)."""
    total = sum(values) or 1
    fig, ax = plt.subplots(figsize=(5.6, 3.0), dpi=150)
    wedges, _texts, autotexts = ax.pie(
        values, colors=hex_colors, startangle=90,
        wedgeprops=dict(width=0.42),
        autopct=lambda pct: f"{pct:.0f}%" if pct >= 5 else "",
        pctdistance=0.78,
        textprops={"fontsize": 8, "color": "white", "weight": "bold"},
    )
    legend_labels = [f"{lbl}  ({v / total * 100:.0f}%)" for lbl, v in zip(labels, values)]
    ax.legend(wedges, legend_labels, loc="center left", bbox_to_anchor=(1.02, 0.5),
              fontsize=8, frameon=False, handlelength=1.2, labelspacing=0.7)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _data_table(headers, rows, col_widths, row_colors=None):
    """Generic small data table: header row (dark) + striped body rows.
    row_colors: optional list of colors.Color, one per row, for the last
    numeric column (used for gainers/losers +/- coloring)."""
    header_row = [Paragraph(h, _head_style) for h in headers]
    data = [header_row]
    for row in rows:
        data.append([Paragraph(str(c), _cell_style) for c in row])

    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), DARK),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e5ea")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
    t.setStyle(TableStyle(style))
    return t


def _movers_table(rows, improved):
    headers = ["Query", "Prev.", "Now", "Change"]
    widths = [95 * mm, 25 * mm, 25 * mm, 25 * mm]
    color_hex = "#33c17a" if improved else "#d84343"
    table_rows = []
    for r in rows:
        change = r["position_change"]
        sign = "+" if change > 0 else ""
        table_rows.append([
            r["query"][:55],
            r["previous_position"],
            r["current_position"],
            f'<font color="{color_hex}">{sign}{change}</font>',
        ])
    return _data_table(headers, table_rows, widths)


def _rank_tracker_table(rows):
    headers = ["Keyword", "Position", "Previous", "Change"]
    widths = [70 * mm, 33 * mm, 33 * mm, 34 * mm]
    table_rows = []
    for r in rows:
        if r["found"] is None:
            position = "Not checked yet"
        elif r["found"]:
            position = r["position"]
        else:
            position = "Not in top 100"
        previous = r["previous_position"] if r["previous_position"] is not None else "—"
        change = r["change"]
        if change is None:
            change_cell = "—"
        elif change == 0:
            change_cell = "No change"
        else:
            improved = change < 0  # negative = position number went down = improved
            color_hex = "#33c17a" if improved else "#d84343"
            arrow = "▲" if improved else "▼"
            change_cell = f'<font color="{color_hex}">{arrow} {abs(change)}</font>'
        table_rows.append([r["keyword"][:45], position, previous, change_cell])
    return _data_table(headers, table_rows, widths)


def generate_pdf(site_url, property_id, start_date, end_date):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=15 * mm,
                             leftMargin=15 * mm, rightMargin=15 * mm)
    elems = []

    title_style = ParagraphStyle("title", parent=_styles["Title"], fontSize=18)
    sub_style = ParagraphStyle("sub", parent=_styles["Normal"], textColor=MUTED, fontSize=9)

    elems.append(Paragraph("SEO &amp; Analytics Report", title_style))
    elems.append(Paragraph(f"{site_url} &nbsp;|&nbsp; {start_date} to {end_date}", sub_style))
    elems.append(Spacer(1, 8 * mm))

    # ==================== Google Analytics ====================
    if property_id:
        ga = ga4_client.get_summary(property_id, start_date, end_date)
        mins, secs = divmod(int(ga["avg_session_duration"]), 60)
        pairs = [
            ("New Users", ga["new_users"]), ("Total Users", ga["total_users"]),
            ("Sessions", ga["sessions"]), ("Views", ga["views"]),
            ("Bounce Rate", f'{ga["bounce_rate"]}%'), ("Engagement Rate", f'{ga["engagement_rate"]}%'),
            ("Events/Session", ga["events_per_session"]), ("Avg. Engagement", f"{mins}:{secs:02d}"),
        ]
        elems.append(_band("Google Analytics"))
        for chunk in _chunk(pairs, 4):
            elems.append(_stat_table(chunk))
            elems.append(Spacer(1, 3 * mm))

        trend = ga4_client.get_trend(property_id, start_date, end_date)
        if trend:
            dates = [r["date"][5:] for r in trend]
            users = [r["active_users"] for r in trend]
            sessions = [r["sessions"] for r in trend]
            img = _line_chart_image(dates, [users, sessions], ["Active Users", "Sessions"],
                                     ["#2f8fd6", "#f5a623"])
            elems.append(Image(img, width=170 * mm, height=58 * mm))
        elems.append(Spacer(1, 4 * mm))

        traffic = ga4_client.get_traffic_sources(property_id, start_date, end_date)
        if traffic:
            elems.append(_sub_title("Traffic Sources"))
            palette = ["#2f8fd6", "#33c17a", "#f5a623", "#e05d5d", "#a566ff", "#26c6da",
                       "#ef8ec9", "#8d99ae", "#c9a227", "#5ad1a3"]
            img = _donut_image(
                [r["channel"] for r in traffic], [r["sessions"] for r in traffic],
                [palette[i % len(palette)] for i in range(len(traffic))]
            )
            elems.append(Image(img, width=140 * mm, height=68 * mm))
        elems.append(Spacer(1, 4 * mm))

        pages = ga4_client.get_top_pages(property_id, start_date, end_date, limit=15)
        if pages:
            elems.append(_sub_title("Top Pages (GA4)"))
            rows = [[p["page"][:70], p["views"], p["users"]] for p in pages]
            elems.append(_data_table(["Page", "Views", "Users"], rows,
                                      [130 * mm, 20 * mm, 20 * mm]))
        elems.append(Spacer(1, 6 * mm))

    # ==================== Google Search Console ====================
    gsc = gsc_client.get_summary(site_url, start_date, end_date)
    pairs = [
        ("Clicks", gsc["clicks"]), ("CTR", f'{gsc["ctr"]}%'),
        ("Avg Position", gsc["position"]), ("Impressions", gsc["impressions"]),
    ]
    elems.append(_band("Google Search Console"))
    elems.append(_stat_table(pairs))
    elems.append(Spacer(1, 3 * mm))

    trend = gsc_client.get_trend(site_url, start_date, end_date)
    if trend:
        dates = [r["date"][5:] for r in trend]
        clicks = [r["clicks"] for r in trend]
        impressions = [r["impressions"] for r in trend]
        img = _line_chart_image(dates, [clicks, impressions], ["Clicks", "Impressions"],
                                 ["#2f8fd6", "#33c17a"])
        elems.append(Image(img, width=170 * mm, height=58 * mm))
    elems.append(Spacer(1, 4 * mm))

    queries = gsc_client.get_queries(site_url, start_date, end_date, limit=20)
    if queries:
        elems.append(_sub_title("Top Queries"))
        rows = [[q["query"][:45], q["clicks"], q["impressions"], f'{q["ctr"]}%', q["position"]]
                for q in queries]
        elems.append(_data_table(["Query", "Clicks", "Impr.", "CTR", "Pos."], rows,
                                  [80 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm]))
    elems.append(Spacer(1, 4 * mm))

    pages = gsc_client.get_pages(site_url, start_date, end_date, limit=20)
    if pages:
        elems.append(_sub_title("Top Pages"))
        rows = [[p["page"][:55], p["clicks"], p["impressions"], f'{p["ctr"]}%', p["position"]]
                for p in pages]
        elems.append(_data_table(["Page", "Clicks", "Impr.", "CTR", "Pos."], rows,
                                  [80 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm]))
    elems.append(Spacer(1, 4 * mm))

    # ---- True Rank Tracker (Serper — live Google position, cached only) ----
    rank_rows = serper_client.get_cached_rankings(site_url)
    if rank_rows:
        elems.append(_sub_title("🔍 True Rank Tracker (Live Google Position)"))
        elems.append(_rank_tracker_table(rank_rows))
        elems.append(Spacer(1, 4 * mm))

    movers = gsc_client.get_movers(site_url, start_date, end_date, limit=10)
    if movers["gainers"]:
        elems.append(_sub_title("📈 Biggest Gainers (position improved)"))
        elems.append(_movers_table(movers["gainers"], improved=True))
        elems.append(Spacer(1, 3 * mm))
    if movers["losers"]:
        elems.append(_sub_title("📉 Biggest Losers (position dropped)"))
        elems.append(_movers_table(movers["losers"], improved=False))
    elems.append(Spacer(1, 4 * mm))

    devices = gsc_client.get_devices(site_url, start_date, end_date)
    if devices:
        elems.append(_sub_title("Devices"))
        rows = [[d["device"], d["clicks"], d["impressions"], f'{d["ctr"]}%', d["position"]]
                for d in devices]
        elems.append(_data_table(["Device", "Clicks", "Impr.", "CTR", "Pos."], rows,
                                  [80 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm]))
    elems.append(Spacer(1, 4 * mm))

    countries = gsc_client.get_countries(site_url, start_date, end_date, limit=15)
    if countries:
        elems.append(_sub_title("Top Countries"))
        rows = [[c["country"], c["clicks"], c["impressions"], f'{c["ctr"]}%', c["position"]]
                for c in countries]
        elems.append(_data_table(["Country", "Clicks", "Impr.", "CTR", "Pos."], rows,
                                  [80 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm]))

    doc.build(elems)
    buf.seek(0)
    return buf