"""
Streamlit monitor for Booking.com listings in DuckDB.

From this folder:
  pip install -r requirements.txt
  streamlit run app.py
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
PAGE_SIZE = 10

DEFAULT_DB_CANDIDATES = [
    os.getenv("DUCKDB_PATH"),
    str(ROOT / "booking.duckdb"),
    str(PROJECT / "booking.duckdb"),
    str(PROJECT / "modal" / "booking.duckdb"),
]


def resolve_db() -> Path | None:
    for raw in DEFAULT_DB_CANDIDATES:
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        if path.exists():
            return path
    return ROOT / "booking.duckdb"


ROOM_TYPE_RE = re.compile(
    r"(Economy Single Room|Standard Single Room|Single Room|Double Room|Twin Room|"
    r"Quadruple Room|Family Room|Deluxe Room|Superior Room|Studio|Apartment|Suite)",
    re.I,
)


def clean_room_type(val) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    m = ROOM_TYPE_RE.search(str(val))
    if m:
        return m.group(1)
    text = " ".join(str(val).split())
    for stop in ("We have", "1 night", "£", "Original price", "twin bed", "double bed"):
        i = text.lower().find(stop.lower())
        if i > 0:
            text = text[:i]
    text = text.strip(" -|,")
    return text[:60] if text else None


def parse_km(val) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*km", str(val), re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_gbp(val) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    m = re.search(r"([\d,]+(?:\.\d{2})?)", str(val))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def is_available_row(row) -> bool:
    text = " ".join(
        str(row.get(c) or "")
        for c in ("rooms_left_hint", "availability_hint", "card_text", "price_current", "price")
    ).lower()
    if re.search(r"sold out|unavailable|fully booked|not available", text):
        return False
    price = row.get("price_num")
    return pd.notna(price)


def add_dod_prices(view: pd.DataFrame, all_rows: pd.DataFrame) -> pd.DataFrame:
    out = view.copy()
    if out.empty or "property_url" not in out.columns:
        out["price_prev"] = pd.NA
        out["price_change"] = pd.NA
        out["price_change_pct"] = pd.NA
        return out

    out["check_in_dt"] = pd.to_datetime(out.get("check_in"), errors="coerce")
    out["room_key"] = out.get("room_type", pd.Series("", index=out.index)).fillna("").astype(str)
    hist = all_rows.copy()
    hist["check_in_dt"] = pd.to_datetime(hist.get("check_in"), errors="coerce")
    hist["room_key"] = hist.get("room_type", pd.Series("", index=hist.index)).fillna("").astype(str)
    prev = (
        hist.dropna(subset=["check_in_dt", "property_url"])
        .sort_values("captured_at" if "captured_at" in hist.columns else "check_in_dt")
        .drop_duplicates(subset=["property_url", "room_key", "check_in_dt"], keep="last")[
            ["property_url", "room_key", "check_in_dt", "price_num"]
        ]
        .rename(columns={"check_in_dt": "prev_dt", "price_num": "price_prev"})
    )
    out["prev_dt"] = out["check_in_dt"] - pd.Timedelta(days=1)
    out = out.merge(prev, how="left", on=["property_url", "room_key", "prev_dt"])
    out["price_change"] = out["price_num"] - out["price_prev"]
    out["price_change_pct"] = (out["price_change"] / out["price_prev"]) * 100
    return out


@st.cache_data(ttl=30)
def load_listings(db_path: str) -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame()
    con = duckdb.connect(db_path, read_only=True)
    try:
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        if "search_listings" not in tables:
            return pd.DataFrame()
        df = con.execute("SELECT * FROM search_listings").fetchdf()
    finally:
        con.close()
    if df.empty:
        return df
    df["price_num"] = df.get("price_current", pd.Series(dtype=str)).map(parse_gbp)
    if "price" in df.columns:
        df["price_num"] = df["price_num"].fillna(df["price"].map(parse_gbp))
    df["review_score_num"] = pd.to_numeric(df.get("review_score"), errors="coerce")
    df["stars"] = pd.to_numeric(df.get("stars"), errors="coerce")
    df["distance_km"] = df.get("distance", pd.Series(dtype=str)).map(parse_km)
    if "room_type" in df.columns:
        df["room_type"] = df["room_type"].map(clean_room_type)
    df["available"] = df.apply(is_available_row, axis=1)
    sort_cols = [c for c in ("captured_at", "ingested_at") if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols)
    keys = [c for c in ("property_url", "check_in", "room_type") if c in df.columns]
    if keys:
        tmp = df.copy()
        if "room_type" in tmp.columns:
            tmp["_room"] = tmp["room_type"].fillna("")
            keys = ["property_url", "check_in", "_room"] if "check_in" in tmp.columns else ["property_url", "_room"]
            df = tmp.drop_duplicates(subset=keys, keep="last").drop(columns=["_room"])
        else:
            df = df.drop_duplicates(subset=keys, keep="last")
    return df


def inject_css():
    st.markdown(
        """
        <style>
          .stApp {
            background: linear-gradient(180deg, #eef3fb 0%, #e8eef8 42%, #f6f8fc 100%);
          }
          .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2.2rem;
            max-width: 1400px;
          }
          header[data-testid="stHeader"] {
            background: transparent;
          }
          [data-testid="stSidebarCollapseButton"],
          [data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"],
          [data-testid="stSidebarCollapseButton"] svg,
          [data-testid="stSidebarCollapseButton"] span {
            color: #ffffff !important;
            fill: #ffffff !important;
          }
          [data-testid="stSidebarCollapseButton"] {
            background: rgba(255,255,255,0.14) !important;
            border-radius: 8px !important;
          }
          [data-testid="collapsedControl"] [data-testid="stIconMaterial"],
          [data-testid="collapsedControl"] svg,
          [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {
            color: #0f2744 !important;
            fill: #0f2744 !important;
          }
          section[data-testid="stSidebar"] [data-testid="stDateInput"] button,
          section[data-testid="stSidebar"] [data-testid="stDateInput"] svg,
          section[data-testid="stSidebar"] [data-testid="stDateInput"] [data-testid="stIconMaterial"],
          section[data-testid="stSidebar"] [data-testid="stDateInput"] [data-baseweb="icon"],
          section[data-testid="stSidebar"] [data-testid="stDateInput"] [data-baseweb="icon"] * {
            color: #0f2744 !important;
            fill: #0f2744 !important;
          }
          section[data-testid="stSidebar"] [data-testid="stDateInputField"] {
            position: relative !important;
            padding-right: 2.2rem !important;
            overflow: visible !important;
          }
          section[data-testid="stSidebar"] [data-testid="stDateInputField"]::after {
            content: "";
            position: absolute;
            right: 12px;
            top: 50%;
            width: 18px;
            height: 18px;
            transform: translateY(-50%);
            pointer-events: none;
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%230f2744' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='4' width='18' height='18' rx='2'/%3E%3Cline x1='16' y1='2' x2='16' y2='6'/%3E%3Cline x1='8' y1='2' x2='8' y2='6'/%3E%3Cline x1='3' y1='10' x2='21' y2='10'/%3E%3C/svg%3E") no-repeat center / 18px 18px;
          }
          section[data-testid="stSidebar"] [data-baseweb="select"] svg,
          section[data-testid="stSidebar"] [data-baseweb="select"] [data-baseweb="icon"],
          section[data-testid="stSidebar"] [data-baseweb="select"] [data-baseweb="icon"] * {
            color: #0f2744 !important;
            fill: #0f2744 !important;
          }
          .stAppDeployButton,
          div[data-testid="stStatusWidget"] {
            visibility: hidden;
          }
          [data-testid="stDataFrame"] table thead th {
            background: #003580 !important;
            color: #fff !important;
            font-weight: 700 !important;
          }
          [data-testid="stDataFrame"] table tbody tr:nth-child(even) td {
            background: #f5f8fd !important;
          }
          section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #062047 0%, #0b2c5c 55%, #0e3a78 100%);
            border-right: 0;
            box-shadow: 8px 0 28px rgba(6, 32, 71, 0.18);
          }
          section[data-testid="stSidebar"] .block-container {
            padding-top: 1.2rem;
          }
          section[data-testid="stSidebar"] h2 {
            font-size: 1.05rem !important;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            color: #ffffff !important;
          }
          section[data-testid="stSidebar"] label,
          section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
            color: #c5d4ea !important;
          }
          section[data-testid="stSidebar"] [data-baseweb="select"] > div,
          section[data-testid="stSidebar"] input,
          section[data-testid="stSidebar"] [data-baseweb="input"],
          section[data-testid="stSidebar"] [data-baseweb="base-input"] {
            background: #ffffff !important;
            color: #0f2744 !important;
            border-radius: 10px !important;
            border: 1px solid #d7e3f4 !important;
            box-shadow: none !important;
          }
          section[data-testid="stSidebar"] [data-baseweb="select"] *,
          section[data-testid="stSidebar"] input,
          section[data-testid="stSidebar"] [data-baseweb="input"] *,
          section[data-testid="stSidebar"] [data-baseweb="tag"] * {
            color: #0f2744 !important;
          }
          section[data-testid="stSidebar"] [data-testid="stDateInput"] input {
            color: #0f2744 !important;
            background: #ffffff !important;
          }
          [data-baseweb="calendar"] {
            color: #0f2744 !important;
          }
          section[data-testid="stSidebar"] [data-testid="stSlider"] p,
          section[data-testid="stSidebar"] [data-testid="stSlider"] span {
            color: #dce8f8 !important;
          }
          section[data-testid="stSidebar"] [data-testid="stTextInput"] input {
            padding-right: 2.2rem !important;
          }
          .page-title {
            margin: 0 0 0.85rem 0 !important;
            font-size: 1.2rem !important;
            font-weight: 700 !important;
            color: #0f2744 !important;
            letter-spacing: 0.2px;
            background: none !important;
            padding: 0 !important;
            line-height: 1.3 !important;
          }
          .kpi-wrap {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 14px;
            margin: 0 0 1.25rem 0;
          }
          .kpi {
            border-radius: 16px;
            padding: 16px 18px 15px;
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.10);
            border: 1px solid rgba(255,255,255,0.55);
            position: relative;
            overflow: hidden;
          }
          .kpi:hover {
            transform: translateY(-2px);
            box-shadow: 0 16px 30px rgba(15, 23, 42, 0.14);
          }
          .kpi .lbl {
            font-size: 0.74rem;
            font-weight: 700;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            margin-bottom: 7px;
            opacity: 0.82;
          }
          .kpi .val {
            font-size: 1.58rem;
            font-weight: 800;
            line-height: 1.15;
          }
          .kpi.navy {
            background: linear-gradient(180deg, #e8f0ff 0%, #ffffff 70%);
            color: #003580;
            box-shadow: 0 10px 24px rgba(0, 53, 128, 0.16);
          }
          .kpi.green {
            background: linear-gradient(180deg, #e6f7f1 0%, #ffffff 70%);
            color: #0f766e;
            box-shadow: 0 10px 24px rgba(15, 118, 110, 0.16);
          }
          .kpi.red {
            background: linear-gradient(180deg, #fdeceb 0%, #ffffff 70%);
            color: #b91c1c;
            box-shadow: 0 10px 24px rgba(185, 28, 28, 0.14);
          }
          .kpi.gold {
            background: linear-gradient(180deg, #fff6d9 0%, #ffffff 70%);
            color: #92400e;
            box-shadow: 0 10px 24px rgba(217, 119, 6, 0.16);
          }
          .kpi.blue {
            background: linear-gradient(180deg, #e6f4ff 0%, #ffffff 70%);
            color: #0369a1;
            box-shadow: 0 10px 24px rgba(3, 105, 161, 0.16);
          }
          .table-title {
            margin: 0 0 0.65rem 0 !important;
            color: #0f2744;
            font-weight: 750 !important;
            font-size: 1.15rem;
          }
          [data-testid="stDataFrame"] {
            border-radius: 14px;
            overflow: hidden;
            border: 1px solid #d7e3f4;
            box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
            background: #fff;
          }
          [data-testid="stDataFrame"] thead tr th,
          [data-testid="stDataFrame"] [role="columnheader"] {
            background: #003580 !important;
            color: #fff !important;
            font-weight: 700 !important;
          }
          .stDownloadButton button {
            background: linear-gradient(135deg, #003580 0%, #0071c2 100%) !important;
            color: #fff !important;
            border: 0 !important;
            border-radius: 11px !important;
            box-shadow: 0 8px 18px rgba(0, 53, 128, 0.28);
            font-weight: 700 !important;
            padding: 0.45rem 1.1rem !important;
          }
          .stDownloadButton button:hover {
            filter: brightness(1.08);
          }
          .pager-info {
            text-align: center;
            color: #334155;
            font-weight: 600;
            padding-top: 0.45rem;
          }
          @media (max-width: 1100px) {
            .kpi-wrap { grid-template-columns: repeat(2, minmax(0, 1fr)); }
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def kpi_row(items: list[tuple[str, str, str]]):
    cells = "".join(
        f'<div class="kpi {cls}"><div class="lbl">{lbl}</div><div class="val">{val}</div></div>'
        for lbl, val, cls in items
    )
    st.markdown(f'<div class="kpi-wrap">{cells}</div>', unsafe_allow_html=True)


st.set_page_config(page_title="Booking.com - Data Monitor", layout="wide")
inject_css()
st.markdown(
    '<div class="page-title">Booking.com - Data Monitor</div>',
    unsafe_allow_html=True,
)

df = load_listings(str(resolve_db()))
if df.empty:
    st.warning("No listings yet. Run a Milan load first.")
    st.stop()

with st.sidebar:
    st.header("Filters")
    cities = sorted(df["city"].dropna().astype(str).unique().tolist()) if "city" in df.columns else []
    city = st.selectbox("City", ["All"] + cities)

    ci_dates = (
        pd.to_datetime(df["check_in"], errors="coerce").dropna().dt.date
        if "check_in" in df.columns
        else pd.Series(dtype=object)
    )
    if ci_dates.empty:
        check_in = st.date_input("Check In", value=None, format="DD/MM/YYYY")
    else:
        check_in = st.date_input(
            "Check In",
            value=min(ci_dates),
            min_value=min(ci_dates),
            max_value=max(ci_dates),
            format="DD/MM/YYYY",
        )

    name_q = st.text_input("Property name", key="property_name", placeholder="Search by name")
    components.html(
        """
        <script>
        (function() {
          const doc = window.parent.document;
          const wire = () => {
            const el = doc.querySelector('.st-key-property_name input');
            if (!el) return;
            if (el.type !== 'search') el.type = 'search';
            const wrap = el.closest('[data-testid="stTextInput"]') || el.parentElement;
            if (!wrap) return;
            wrap.style.position = 'relative';
            let btn = wrap.querySelector('.name-clear-x');
            if (!btn) {
              btn = doc.createElement('button');
              btn.className = 'name-clear-x';
              btn.type = 'button';
              btn.setAttribute('aria-label', 'Clear search');
              btn.textContent = '×';
              btn.style.cssText = [
                'position:absolute',
                'right:10px',
                'bottom:8px',
                'width:22px',
                'height:22px',
                'border:0',
                'border-radius:999px',
                'background:#94a3b8',
                'color:#fff',
                'font-size:16px',
                'line-height:20px',
                'cursor:pointer',
                'z-index:6',
                'padding:0'
              ].join(';');
              btn.addEventListener('click', (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                const last = el.value;
                el.value = '';
                const tracker = el._valueTracker;
                if (tracker) tracker.setValue(last);
                el.dispatchEvent(new doc.defaultView.Event('input', { bubbles: true }));
                el.dispatchEvent(new doc.defaultView.Event('change', { bubbles: true }));
                btn.style.display = 'none';
              });
              wrap.appendChild(btn);
              el.addEventListener('input', () => {
                btn.style.display = el.value ? 'block' : 'none';
              });
            }
            btn.style.display = el.value ? 'block' : 'none';
          };
          wire();
          new doc.defaultView.MutationObserver(wire).observe(doc.body, { childList: true, subtree: true });
        })();
        </script>
        """,
        height=0,
    )

    rooms = sorted(df["room_type"].dropna().astype(str).unique().tolist()) if "room_type" in df.columns else []
    room_type = st.multiselect("Room Type", rooms)

    sponsored_opt = st.selectbox("Sponsored", ["All", "Yes", "No"])

    review_range = st.slider(
        "Review score",
        min_value=0.0,
        max_value=10.0,
        value=(0.0, 10.0),
        step=0.1,
    )

    price_range = st.slider(
        "Price Range (£)",
        min_value=10,
        max_value=10000,
        value=(10, 10000),
        step=10,
    )

    distance_range = st.slider(
        "Distance (km from centre)",
        min_value=0.0,
        max_value=50.0,
        value=(0.0, 50.0),
        step=0.5,
    )

view = df.copy()
if city != "All" and "city" in view.columns:
    view = view[view["city"].astype(str) == city]
if check_in and "check_in" in view.columns:
    view = view[pd.to_datetime(view["check_in"], errors="coerce").dt.date == check_in]
if name_q and "title" in view.columns:
    view = view[view["title"].fillna("").astype(str).str.contains(name_q, case=False, na=False)]
if room_type and "room_type" in view.columns:
    view = view[view["room_type"].astype(str).isin(room_type)]
if sponsored_opt != "All" and "sponsored" in view.columns:
    view = view[view["sponsored"] == (sponsored_opt == "Yes")]
if "review_score_num" in view.columns:
    rlo, rhi = review_range
    view = view[
        view["review_score_num"].isna()
        | ((view["review_score_num"] >= rlo) & (view["review_score_num"] <= rhi))
    ]
if "price_num" in view.columns:
    lo, hi = price_range
    view = view[view["price_num"].isna() | ((view["price_num"] >= lo) & (view["price_num"] <= hi))]
if "distance_km" in view.columns:
    dlo, dhi = distance_range
    view = view[view["distance_km"].isna() | ((view["distance_km"] >= dlo) & (view["distance_km"] <= dhi))]

view = add_dod_prices(view, df)

if "property_url" in view.columns and not view.empty:
    hotel_avail = view.groupby("property_url")["available"].any() if "available" in view.columns else pd.Series(dtype=bool)
    total_props = int(view["property_url"].nunique())
    available_n = int(hotel_avail.sum()) if not hotel_avail.empty else 0
    not_available_n = total_props - available_n
else:
    total_props = len(view)
    available_n = int(view["available"].sum()) if "available" in view.columns else 0
    not_available_n = total_props - available_n
avg_p = view["price_num"].mean() if "price_num" in view.columns else None
pct = view["price_change_pct"].dropna() if "price_change_pct" in view.columns else pd.Series(dtype=float)
avg_pct = pct.mean() if not pct.empty else None

kpi_row(
    [
        ("Total properties", f"{total_props:,}", "navy"),
        ("Available", f"{available_n:,}", "green"),
        ("Not available", f"{not_available_n:,}", "red"),
        ("Avg price", f"£{avg_p:,.0f}" if pd.notna(avg_p) else "—", "gold"),
        ("Avg change %", f"{avg_pct:+.1f}%" if pd.notna(avg_pct) else "—", "blue"),
    ]
)

st.markdown(
    '<h3 class="table-title">Listings</h3>',
    unsafe_allow_html=True,
)
table = pd.DataFrame(
    {
        "Property Name": view.get("title"),
        "Room Type": view.get("room_type"),
        "Bed": view.get("bed_info"),
        "Original price": view.get("price_original"),
        "Price Change": view.get("price_change"),
        "Change %": view.get("price_change_pct"),
        "Discounted Price": view.get("price_current", view.get("price")),
        "Tax": view.get("taxes_fees"),
        "Review": view.get("review_word"),
        "Review Score": view.get("review_score"),
        "Reviews": view.get("review_count"),
        "Distance": view.get("distance"),
        "Available": view.get("available"),
        "Sponsored": view.get("sponsored"),
        "Genius": view.get("genius"),
    }
)
if "Change %" in table.columns:
    table["Change %"] = pd.to_numeric(table["Change %"], errors="coerce").round(1)
if "Price Change" in table.columns:
    table["Price Change"] = pd.to_numeric(table["Price Change"], errors="coerce").round(2)
if "Review Score" in table.columns:
    table["Review Score"] = pd.to_numeric(table["Review Score"], errors="coerce")
for flag in ("Available", "Sponsored", "Genius"):
    if flag in table.columns:
        table[flag] = table[flag].fillna(False).astype(bool)


def _chg_style(val):
    if pd.isna(val):
        return ""
    if val > 0:
        return "color: #c1121f; font-weight: 600"
    if val < 0:
        return "color: #1b7f5a; font-weight: 600"
    return ""


filter_key = (
    city,
    check_in,
    name_q,
    tuple(room_type),
    sponsored_opt,
    review_range,
    price_range,
    distance_range,
)
if st.session_state.get("_filter_key") != filter_key:
    st.session_state._filter_key = filter_key
    st.session_state.listings_page = 1

total_rows = len(table)
total_pages = max(1, (total_rows + PAGE_SIZE - 1) // PAGE_SIZE)
page = min(max(1, int(st.session_state.get("listings_page", 1))), total_pages)
st.session_state.listings_page = page
start = (page - 1) * PAGE_SIZE
page_table = table.iloc[start : start + PAGE_SIZE].reset_index(drop=True)

style_cols = [c for c in ("Price Change", "Change %") if c in page_table.columns]
styled = page_table.style.map(_chg_style, subset=style_cols) if style_cols else page_table.style
st.dataframe(
    styled,
    use_container_width=True,
    hide_index=True,
    height=420,
    column_config={
        "Available": st.column_config.CheckboxColumn("Available", disabled=True),
        "Sponsored": st.column_config.CheckboxColumn("Sponsored", disabled=True),
        "Genius": st.column_config.CheckboxColumn("Genius", disabled=True),
        "Review Score": st.column_config.NumberColumn("Review Score", format="%.1f"),
        "Price Change": st.column_config.NumberColumn("Price Change", format="£%.2f"),
        "Change %": st.column_config.NumberColumn("Change %", format="%.1f%%"),
    },
)

prev_col, info_col, next_col = st.columns([1, 2, 1])
with prev_col:
    if st.button("← Previous", disabled=page <= 1, use_container_width=True):
        st.session_state.listings_page = page - 1
        st.rerun()
with info_col:
    shown_from = 0 if total_rows == 0 else start + 1
    shown_to = min(start + PAGE_SIZE, total_rows)
    st.markdown(
        f'<div class="pager-info">Page {page} of {total_pages} · {shown_from}–{shown_to} of {total_rows:,}</div>',
        unsafe_allow_html=True,
    )
with next_col:
    if st.button("Next →", disabled=page >= total_pages, use_container_width=True):
        st.session_state.listings_page = page + 1
        st.rerun()

st.download_button(
    "Download filtered CSV",
    table.to_csv(index=False).encode("utf-8"),
    file_name="listings_filtered.csv",
    mime="text/csv",
)
