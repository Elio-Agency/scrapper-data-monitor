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

import altair as alt
import duckdb
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
PAGE_SIZE = 10

# Milan CAP (postal code) → Municipio. Official city districts, not Booking neighbourhood tags.
MUNI_LABEL = {
    1: "M1 Centro",
    2: "M2 Centrale / Greco",
    3: "M3 Città Studi",
    4: "M4 Porta Romana",
    5: "M5 Vigentino",
    6: "M6 Navigli",
    7: "M7 San Siro",
    8: "M8 Fiera / CityLife",
    9: "M9 Garibaldi / Niguarda",
}
MUNI_ORDER = [MUNI_LABEL[i] for i in range(1, 10)]
CAP_TO_MUNI = {
    "20121": 1, "20122": 1, "20123": 1,
    "20124": 2, "20127": 2, "20128": 2,
    "20125": 9, "20126": 9,
    "20129": 3, "20131": 3, "20132": 3, "20133": 3,
    "20134": 4, "20135": 4, "20137": 4, "20138": 4,
    "20139": 5, "20141": 5,
    "20136": 6, "20142": 6, "20143": 6, "20144": 6,
    "20146": 7, "20147": 7, "20152": 7, "20153": 7,
    "20145": 8, "20148": 8, "20149": 8, "20151": 8, "20154": 8, "20156": 8, "20157": 8,
    "20155": 9, "20158": 9, "20159": 9, "20161": 9, "20162": 9,
}


def district_from_postal(val) -> str | None:
    cap = clean_postal(val)
    if not cap:
        return None
    n = CAP_TO_MUNI.get(cap)
    if n is None:
        return None
    return MUNI_LABEL[n]

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
    text = str(val)
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*km", text, re.I)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*m\b", text, re.I)
    if m:
        try:
            return float(m.group(1).replace(",", "")) / 1000.0
        except ValueError:
            return None
    return None


def parse_district(val) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    text = " ".join(str(val).split())
    if not text or text.lower() in ("nan", "none"):
        return None
    text = re.sub(
        r"Featured This property matches.*?commission if you make a booking\.\s*",
        " ",
        text,
        flags=re.I,
    )
    hits = re.findall(
        r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 .'/&-]{1,40}),\s*Milan\b",
        text,
        flags=re.I,
    )
    name = hits[-1].strip() if hits else None
    if not name:
        m = re.search(r",\s*([^,]+),\s*\d{5}\s+Milan\b", text, flags=re.I)
        if m:
            name = m.group(1).strip()
    if not name or re.match(r"^\d", name) or len(name) > 40:
        return None
    name = re.sub(r"^Opens in new window\s+", "", name, flags=re.I)
    name = re.sub(r"^Milan\s+", "", name, flags=re.I).strip(" -")
    if not name:
        return None
    if name.lower() in {"city centre", "milan city centre"}:
        return "City Centre"
    return name


def district_from_row(row) -> str | None:
    try:
        cap = row["postal_code"]
    except Exception:
        cap = row.get("postal_code") if hasattr(row, "get") else None
    return district_from_postal(cap)


def parse_bed_band(val) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    m = re.search(r"(\d+)\s*(?:single |double |sofa |twin |king |queen )?beds?", str(val), re.I)
    if not m:
        return None
    n = int(m.group(1))
    if n <= 0:
        return None
    if n >= 4:
        return "4+"
    return str(n)


def distance_band(km) -> str | None:
    if km is None or (isinstance(km, float) and pd.isna(km)):
        return None
    try:
        n = float(km)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return "0–1 km"
    if n < 3:
        return "1–3 km"
    if n < 5:
        return "3–5 km"
    return "5 km+"


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


def clean_postal(val) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if s.lower() in ("", "nan", "none"):
        return None
    m = re.match(r"^(\d+)\.0+$", s)
    if m:
        return m.group(1)
    return s


def clean_int_count(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return pd.NA
    s = str(val).strip().replace(",", "")
    if s.lower() in ("", "nan", "none"):
        return pd.NA
    m = re.match(r"^(\d+)\.0+$", s)
    if m:
        return int(m.group(1))
    try:
        n = float(s)
    except ValueError:
        return pd.NA
    if pd.isna(n):
        return pd.NA
    return int(n)


def parse_tax_text(*parts) -> str:
    blob = " ".join(
        str(p).strip()
        for p in parts
        if p is not None and not (isinstance(p, float) and pd.isna(p)) and str(p).strip().lower() not in ("", "nan", "none")
    )
    if not blob:
        return ""
    m = re.search(r"\+?\s*£\s*([\d,]+(?:\.\d{2})?)\s*taxes", blob, re.I)
    if m:
        return f"+£{m.group(1)}"
    if re.search(r"includes taxes", blob, re.I):
        return "Included"
    m = re.search(r"(\+£[\d,]+(?:\.\d{2})?)", blob)
    if m and re.search(r"tax", blob, re.I):
        return m.group(1)
    if re.search(r"tax", blob, re.I) and len(blob) < 80:
        return blob
    return ""


def tax_display(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=str)
    taxes = frame["taxes_fees"] if "taxes_fees" in frame.columns else pd.Series("", index=frame.index)
    hint = frame["rooms_left_hint"] if "rooms_left_hint" in frame.columns else pd.Series("", index=frame.index)
    avail = frame["availability_hint"] if "availability_hint" in frame.columns else pd.Series("", index=frame.index)
    return pd.Series(
        [parse_tax_text(t, h, a) for t, h, a in zip(taxes, hint, avail)],
        index=frame.index,
    )


def amenity_list(val) -> list[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return []
    parts = [p.strip() for p in str(val).replace("|", ";").split(";")]
    out = []
    seen = set()
    for p in parts:
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def unique_amenities(frame: pd.DataFrame) -> list[str]:
    names: set[str] = set()
    for col in ("popular_facilities", "amenities"):
        if col not in frame.columns:
            continue
        for val in frame[col].dropna():
            names.update(amenity_list(val))
    return sorted(names)


def row_has_amenities(row, selected: list[str]) -> bool:
    have: set[str] = set()
    for col in ("popular_facilities", "amenities"):
        have.update(a.lower() for a in amenity_list(row.get(col)))
    return all(a.lower() in have for a in selected)


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
    if "postal_code" in df.columns:
        df["postal_code"] = df["postal_code"].map(clean_postal)
    df["district"] = df["postal_code"].map(district_from_postal) if "postal_code" in df.columns else None  # v3: CAP municipi
    if "bed_info" in df.columns:
        df["bed_band"] = df["bed_info"].map(parse_bed_band)
    df["distance_band"] = df["distance_km"].map(distance_band)
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


NAVY = "#003580"
GOLD = "#febb02"
DIST_ORDER = ["0–1 km", "1–3 km", "3–5 km", "5 km+"]
BED_ORDER = ["1", "2", "3", "4+"]
BED_LABEL = {"1": "1 bed", "2": "2 beds", "3": "3 beds", "4+": "4+ beds"}
BED_LABEL_ORDER = ["1 bed", "2 beds", "3 beds", "4+ beds"]
BED_COLORS = ["#003580", "#0071c2", "#7ba3d4", "#c5d8ef"]


def unique_hotels(view: pd.DataFrame) -> pd.DataFrame:
    if view.empty or "property_url" not in view.columns:
        return pd.DataFrame()
    agg = {}
    for col, how in (
        ("title", "last"),
        ("price_num", "median"),
        ("review_score_num", "median"),
        ("distance_km", "median"),
        ("district", "last"),
        ("review_count", "last"),
        ("distance_band", "last"),
    ):
        if col in view.columns:
            agg[col] = how
    if not agg:
        return view.drop_duplicates("property_url")
    return view.groupby("property_url", as_index=False).agg(agg)


def _style_chart(chart: alt.Chart) -> alt.Chart:
    return (
        chart.configure_view(strokeWidth=0)
        .configure_axis(
            labelColor="#334155",
            titleColor="#0f2744",
            gridColor="#e8eef6",
            domainColor="#cbd5e1",
            labelFontSize=11,
            titleFontSize=12,
        )
        .configure_legend(labelColor="#334155", titleColor="#0f2744")
        .configure_title(color="#0f2744", fontSize=13, fontWeight=700, anchor="start")
    )


def _show_chart(chart: alt.Chart | None, empty_msg: str, heading: str) -> None:
    with st.container(border=True):
        st.markdown(f'<div class="chart-heading">{heading}</div>', unsafe_allow_html=True)
        if chart is None:
            st.caption(empty_msg)
            return
        st.altair_chart(_style_chart(chart), use_container_width=True, theme=None)


def _district_chart(hotels: pd.DataFrame) -> alt.Chart | None:
    if hotels.empty or "district" not in hotels.columns:
        return None
    stats = (
        hotels.dropna(subset=["district"])
        .groupby("district", as_index=False)
        .agg(hotels=("property_url", "count"), avg_price=("price_num", "mean"))
    )
    stats = stats[stats["district"].isin(MUNI_ORDER)]
    if stats.empty:
        return None
    bars = (
        alt.Chart(stats)
        .mark_bar(color=NAVY, size=18, cornerRadiusEnd=3)
        .encode(
            x=alt.X(
                "district:N",
                sort=MUNI_ORDER,
                title=None,
                axis=alt.Axis(labelAngle=-30, labelLimit=160),
            ),
            y=alt.Y("hotels:Q", title="Hotels"),
            tooltip=[
                alt.Tooltip("district:N", title="District"),
                alt.Tooltip("hotels:Q", title="Hotels"),
                alt.Tooltip("avg_price:Q", title="Avg £", format=".0f"),
            ],
        )
    )
    line = (
        alt.Chart(stats)
        .mark_line(color=GOLD, strokeWidth=2.5)
        .encode(
            x=alt.X("district:N", sort=MUNI_ORDER),
            y=alt.Y("avg_price:Q", title="Avg £"),
        )
    )
    pts = (
        alt.Chart(stats)
        .mark_circle(color=GOLD, size=70)
        .encode(
            x=alt.X("district:N", sort=MUNI_ORDER),
            y=alt.Y("avg_price:Q", title="Avg £"),
            tooltip=[
                alt.Tooltip("district:N", title="District"),
                alt.Tooltip("hotels:Q", title="Hotels"),
                alt.Tooltip("avg_price:Q", title="Avg £", format=".0f"),
            ],
        )
    )
    return (
        alt.layer(bars, line, pts)
        .resolve_scale(y="independent")
        .properties(height=280)
    )


def _bedroom_chart(view: pd.DataFrame) -> alt.Chart | None:
    if view.empty or "bed_band" not in view.columns:
        return None
    stats = (
        view.dropna(subset=["bed_band"])["bed_band"]
        .value_counts()
        .rename_axis("bed_band")
        .reset_index(name="listings")
    )
    stats = stats[stats["bed_band"].isin(BED_ORDER)]
    if stats.empty:
        return None
    stats["bed_label"] = stats["bed_band"].map(BED_LABEL)
    stats["pct"] = stats["listings"] / stats["listings"].sum()
    stats["bar_label"] = [
        f"{int(n)}  ({p:.0%})" for n, p in zip(stats["listings"], stats["pct"])
    ]
    x_max = float(stats["listings"].max()) * 1.22
    bars = (
        alt.Chart(stats)
        .mark_bar(size=22, cornerRadiusEnd=3)
        .encode(
            y=alt.Y("bed_label:N", sort=BED_LABEL_ORDER, title="Bedrooms"),
            x=alt.X("listings:Q", title="Listings", scale=alt.Scale(domain=[0, x_max])),
            color=alt.Color(
                "bed_label:N",
                sort=BED_LABEL_ORDER,
                scale=alt.Scale(domain=BED_LABEL_ORDER, range=BED_COLORS),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("bed_label:N", title="Bedrooms"),
                alt.Tooltip("listings:Q", title="Listings"),
                alt.Tooltip("pct:Q", title="Share", format=".1%"),
            ],
        )
    )
    labels = (
        alt.Chart(stats)
        .mark_text(align="left", dx=6, fontSize=12, fontWeight=600)
        .encode(
            y=alt.Y("bed_label:N", sort=BED_LABEL_ORDER),
            x=alt.X("listings:Q"),
            text=alt.Text("bar_label:N"),
            color=alt.value("#0f2744"),
        )
    )
    return alt.layer(bars, labels).properties(height=280)


def _price_review_chart(hotels: pd.DataFrame) -> alt.Chart | None:
    if hotels.empty:
        return None
    pts = hotels.dropna(subset=["price_num", "review_score_num"]).copy()
    if pts.empty:
        return None
    cap = pts["price_num"].quantile(0.98)
    if pd.notna(cap) and cap > 0:
        pts = pts[pts["price_num"] <= cap]
    if "title" not in pts.columns:
        pts["title"] = pts.get("property_url", "")
    return (
        alt.Chart(pts)
        .mark_circle(size=55, opacity=0.45, color=NAVY)
        .encode(
            x=alt.X("review_score_num:Q", title="Review score", scale=alt.Scale(zero=False)),
            y=alt.Y("price_num:Q", title="Price (£)"),
            tooltip=[
                alt.Tooltip("title:N", title="Hotel"),
                alt.Tooltip("district:N", title="District"),
                alt.Tooltip("price_num:Q", title="Price £", format=".0f"),
                alt.Tooltip("review_score_num:Q", title="Score", format=".1f"),
            ],
        )
        .properties(height=280)
        .interactive()
    )


def _distance_price_chart(hotels: pd.DataFrame) -> alt.Chart | None:
    if hotels.empty or "price_num" not in hotels.columns:
        return None
    d = hotels.dropna(subset=["price_num"]).copy()
    if "distance_band" not in d.columns and "distance_km" in d.columns:
        d["distance_band"] = d["distance_km"].map(distance_band)
    if "distance_band" not in d.columns:
        return None
    stats = (
        d.dropna(subset=["distance_band"])
        .groupby("distance_band", as_index=False)
        .agg(avg_price=("price_num", "mean"), hotels=("property_url", "count"))
    )
    stats = stats[stats["distance_band"].isin(DIST_ORDER)]
    if stats.empty:
        return None
    return (
        alt.Chart(stats)
        .mark_line(color=GOLD, strokeWidth=3, point=alt.OverlayMarkDef(filled=True, size=90, color=GOLD))
        .encode(
            x=alt.X("distance_band:N", sort=DIST_ORDER, title="Distance from centre"),
            y=alt.Y("avg_price:Q", title="Avg price (£)"),
            tooltip=[
                alt.Tooltip("distance_band:N", title="Band"),
                alt.Tooltip("avg_price:Q", title="Avg £", format=".0f"),
                alt.Tooltip("hotels:Q", title="Hotels"),
            ],
        )
        .properties(height=280)
    )


def render_pattern_charts(view: pd.DataFrame) -> None:
    hotels = unique_hotels(view)
    row1_left, row1_right = st.columns(2)
    with row1_left:
        _show_chart(_district_chart(hotels), "No postal-code area data in the current filters.", "Hotels by Area")
    with row1_right:
        _show_chart(_distance_price_chart(hotels), "No distance data in the current filters.", "Average price by distance")
    row2_left, row2_right = st.columns(2)
    with row2_left:
        _show_chart(_bedroom_chart(view), "No bedroom data in the current filters.", "Listings by Bedroom")
    with row2_right:
        _show_chart(_price_review_chart(hotels), "No review scores in the current filters.", "Price vs review score")


def inject_css():
    st.markdown(
        """
        <style>
          .stApp {
            background: linear-gradient(180deg, #eef3fb 0%, #f4f7fb 100%);
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
            background: rgba(255,255,255,0.12) !important;
            border: 1px solid rgba(255,255,255,0.16) !important;
            border-radius: 10px !important;
            width: 34px !important;
            height: 34px !important;
          }
          .stApp:has([data-testid="stExpandSidebarButton"])::before {
            content: "";
            position: fixed;
            left: 0;
            top: 0;
            bottom: 0;
            width: 36px;
            background: #003580;
            border-radius: 0 14px 14px 0;
            box-shadow: 6px 0 16px rgba(0, 53, 128, 0.16);
            z-index: 1000000;
            pointer-events: none;
          }
          .stApp:has([data-testid="stExpandSidebarButton"])::after {
            content: "";
            position: fixed;
            left: 0;
            top: 50%;
            width: 36px;
            height: 36px;
            transform: translateY(-50%);
            z-index: 1000004;
            pointer-events: none;
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'%3E%3Cpath d='M8 5l8 7-8 7' stroke='%23ffffff' stroke-width='2.8' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat center;
            background-size: 20px 20px;
          }
          .stApp:has([data-testid="stExpandSidebarButton"]) [data-testid="stMain"],
          .stApp:has([data-testid="stExpandSidebarButton"]) section.main,
          .stApp:has([data-testid="stExpandSidebarButton"]) [data-testid="stAppViewContainer"] > .main {
            margin-left: 36px !important;
          }
          .stApp:has([data-testid="stExpandSidebarButton"]) header[data-testid="stHeader"] {
            left: 36px !important;
            width: calc(100% - 36px) !important;
            background: transparent !important;
          }
          [data-testid="collapsedControl"],
          [data-testid="stSidebarCollapsedControl"] {
            position: fixed !important;
            left: 0 !important;
            top: 0 !important;
            width: 36px !important;
            min-width: 36px !important;
            padding: 0 !important;
            background: transparent !important;
            z-index: 1000002 !important;
          }
          [data-testid="stExpandSidebarButton"],
          [data-testid="collapsedControl"] button,
          [data-testid="stSidebarCollapsedControl"] button {
            position: fixed !important;
            left: 18px !important;
            top: 50% !important;
            transform: translate(-50%, -50%) !important;
            background: transparent !important;
            color: #ffffff !important;
            width: 36px !important;
            height: 36px !important;
            min-width: 36px !important;
            min-height: 36px !important;
            padding: 0 !important;
            border-radius: 8px !important;
            border: 0 !important;
            box-shadow: none !important;
            cursor: pointer !important;
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
            overflow: visible !important;
            z-index: 1000003 !important;
          }
          [data-testid="stExpandSidebarButton"]:hover,
          [data-testid="collapsedControl"] button:hover,
          [data-testid="stSidebarCollapsedControl"] button:hover {
            background-color: rgba(255,255,255,0.12) !important;
          }
          [data-testid="stExpandSidebarButton"] span,
          [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"],
          [data-testid="stExpandSidebarButton"] svg {
            opacity: 0 !important;
          }
          [data-testid="stExpandSidebarButton"]::after {
            content: none !important;
          }
          [data-testid="collapsedControl"] [data-testid="stIconMaterial"],
          [data-testid="collapsedControl"] svg,
          [data-testid="stSidebarCollapsedControl"] [data-testid="stIconMaterial"],
          [data-testid="stSidebarCollapsedControl"] svg {
            color: #ffffff !important;
            fill: #ffffff !important;
            font-size: 1.2rem !important;
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
            width: 16px;
            height: 16px;
            transform: translateY(-50%);
            pointer-events: none;
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%230f2744' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='4' width='18' height='18' rx='2'/%3E%3Cline x1='16' y1='2' x2='16' y2='6'/%3E%3Cline x1='8' y1='2' x2='8' y2='6'/%3E%3Cline x1='3' y1='10' x2='21' y2='10'/%3E%3C/svg%3E") no-repeat center / 16px 16px;
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
            background: #003580 !important;
            background-image: linear-gradient(180deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0) 160px) !important;
            border-right: 0;
            box-shadow: 10px 0 28px rgba(7, 29, 64, 0.16);
          }
          section[data-testid="stSidebar"] > div:first-child {
            background: transparent !important;
          }
          section[data-testid="stSidebar"] .block-container,
          section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
            padding: 0.85rem 1.05rem 2rem !important;
          }
          section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            gap: 0.28rem !important;
          }
          section[data-testid="stSidebar"] iframe {
            display: none !important;
            height: 0 !important;
          }
          section[data-testid="stSidebar"] .stMarkdown,
          section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
            margin: 0 !important;
          }
          .filter-hero {
            display: block;
            padding: 0.15rem 0 0.9rem;
            margin-bottom: 0.2rem;
            border-bottom: 1px solid rgba(255,255,255,0.10);
          }
          .filter-kicker {
            display: block;
            font-size: 0.66rem;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            color: #febb02;
            font-weight: 700;
            margin-bottom: 0.28rem;
          }
          .filter-title {
            display: block;
            font-size: 1.38rem;
            font-weight: 800;
            color: #ffffff;
            letter-spacing: 0.03em;
            line-height: 1.15;
          }
          .filter-section {
            display: flex;
            align-items: center;
            gap: 8px;
            width: 100%;
            margin: 0;
            padding: 0.85rem 0 0.45rem;
            color: #9fb6d6;
            font-size: 0.68rem;
            font-weight: 700;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            box-sizing: border-box;
          }
          .filter-section::after {
            content: "";
            flex: 1;
            height: 1px;
            background: rgba(255,255,255,0.12);
          }
          section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.filter-section) {
            min-height: 2.4rem;
          }
          section[data-testid="stSidebar"] h2 {
            font-size: 1.05rem !important;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            color: #ffffff !important;
          }
          section[data-testid="stSidebar"] label,
          section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
            color: #d5e3f6 !important;
            font-size: 0.78rem !important;
            font-weight: 600 !important;
            letter-spacing: 0.01em;
            margin-bottom: 0.18rem !important;
          }
          section[data-testid="stSidebar"] [data-baseweb="select"] > div {
            background: #f7fafd !important;
            color: #0f2744 !important;
            border-radius: 9px !important;
            border: 1px solid rgba(255,255,255,0.65) !important;
            box-shadow: 0 1px 0 rgba(7, 29, 64, 0.08) !important;
            min-height: 42px !important;
          }
          section[data-testid="stSidebar"] [data-testid="stTextInput"] input,
          section[data-testid="stSidebar"] [data-testid="stDateInput"] input {
            background: #f7fafd !important;
            color: #0f2744 !important;
            border-radius: 9px !important;
            border: 1px solid rgba(255,255,255,0.65) !important;
            box-shadow: 0 1px 0 rgba(7, 29, 64, 0.08) !important;
            min-height: 42px !important;
          }
          section[data-testid="stSidebar"] [data-baseweb="select"] input {
            min-height: auto !important;
            background: transparent !important;
            border: 0 !important;
            box-shadow: none !important;
          }
          section[data-testid="stSidebar"] [data-baseweb="select"] > div:hover,
          section[data-testid="stSidebar"] [data-testid="stTextInput"] input:hover,
          section[data-testid="stSidebar"] [data-testid="stDateInput"] input:hover {
            border-color: #febb02 !important;
          }
          section[data-testid="stSidebar"] [data-baseweb="select"] > div:focus-within,
          section[data-testid="stSidebar"] [data-testid="stTextInput"] input:focus,
          section[data-testid="stSidebar"] [data-testid="stDateInput"] input:focus {
            border-color: #febb02 !important;
            box-shadow: 0 0 0 2px rgba(254, 187, 2, 0.28) !important;
          }
          section[data-testid="stSidebar"] [data-baseweb="select"] *,
          section[data-testid="stSidebar"] [data-testid="stTextInput"] input,
          section[data-testid="stSidebar"] [data-testid="stDateInput"] input,
          section[data-testid="stSidebar"] [data-baseweb="tag"] * {
            color: #0f2744 !important;
          }
          section[data-testid="stSidebar"] input::placeholder {
            color: #8aa0bd !important;
          }
          section[data-testid="stSidebar"] [data-baseweb="tag"] {
            background: #e8eef8 !important;
            border-radius: 6px !important;
          }
          section[data-testid="stSidebar"] [data-testid="stDateInput"] input {
            color: #0f2744 !important;
            background: #f7fafd !important;
          }
          [data-baseweb="calendar"] {
            color: #0f2744 !important;
          }
          section[data-testid="stSidebar"] [data-testid="stSlider"] p,
          section[data-testid="stSidebar"] [data-testid="stSlider"] span,
          section[data-testid="stSidebar"] [data-testid="stSliderTickBarMin"],
          section[data-testid="stSidebar"] [data-testid="stSliderTickBarMax"] {
            color: #c5d6ee !important;
            font-weight: 600 !important;
          }
          section[data-testid="stSidebar"] [data-baseweb="slider"] [role="slider"] {
            background-color: #febb02 !important;
            border-color: #febb02 !important;
          }
          section[data-testid="stSidebar"] [data-testid="stSlider"] [data-baseweb="slider"] > div > div {
            background: rgba(255,255,255,0.18) !important;
          }
          section[data-testid="stSidebar"] [data-testid="stTextInput"] input {
            padding-right: 2.2rem !important;
          }
          .page-title {
            margin: 0 0 1rem 0 !important;
            font-size: 1.85rem !important;
            font-weight: 800 !important;
            color: #0f2744 !important;
            letter-spacing: 0.2px;
            background: none !important;
            padding: 0 !important;
            line-height: 1.25 !important;
            -webkit-text-fill-color: unset;
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
            box-shadow: 0 8px 20px rgba(15, 23, 42, 0.08);
            border: 1px solid #d7e3f4;
            position: relative;
            overflow: hidden;
            background: #fff;
          }
          .kpi:hover {
            transform: translateY(-1px);
            box-shadow: 0 12px 24px rgba(15, 23, 42, 0.10);
          }
          .kpi .lbl {
            font-size: 0.74rem;
            font-weight: 700;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            margin-bottom: 7px;
            opacity: 0.8;
          }
          .kpi .val {
            font-size: 1.58rem;
            font-weight: 800;
            line-height: 1.15;
          }
          .kpi.navy {
            background: #fff;
            color: #003580;
          }
          .kpi.green {
            background: #fff;
            color: #0f766e;
          }
          .kpi.red {
            background: #fff;
            color: #b91c1c;
          }
          .kpi.gold {
            background: #fff;
            color: #92400e;
          }
          .kpi.blue {
            background: #fff;
            color: #0369a1;
          }
          .table-title {
            margin: 0 0 0.55rem 0 !important;
            color: #0f2744;
            font-weight: 700 !important;
            font-size: 1.08rem;
          }
          .chart-heading {
            margin: 0.05rem 0 0.35rem 0;
            color: #0f2744;
            font-weight: 700;
            font-size: 1.02rem;
            letter-spacing: 0.15px;
            line-height: 1.3;
          }
          [data-testid="stVerticalBlockBorderWrapper"] {
            background: #ffffff !important;
            border: 1px solid #d7e3f4 !important;
            border-radius: 14px !important;
            box-shadow: 0 8px 20px rgba(15, 23, 42, 0.06) !important;
            padding: 0.7rem 0.85rem 0.4rem !important;
          }
          [data-testid="stVegaLiteChart"],
          [data-testid="stArrowVegaLiteChart"] {
            background: transparent;
            border: 0;
            box-shadow: none;
            padding: 0;
            margin-bottom: 0;
          }
          [data-testid="stDataFrame"] {
            border-radius: 10px;
            overflow: hidden;
            border: 1px solid #d7e3f4;
            box-shadow: none;
            background: #fff;
          }
          .listings-table {
            width: 100%;
            overflow-x: auto;
            border-radius: 8px;
            line-height: 1.25;
          }
          .listings-table table {
            width: max-content;
            min-width: 100%;
            border-collapse: collapse;
            height: auto !important;
          }
          .listings-table thead th {
            background: #003580 !important;
            color: #ffffff !important;
            font-weight: 700 !important;
            font-size: 0.75rem !important;
            letter-spacing: 0.03em;
            text-transform: uppercase;
            text-align: left !important;
            padding: 8px 10px !important;
            border: 0 !important;
            white-space: nowrap !important;
            height: auto !important;
            line-height: 1.2 !important;
          }
          .listings-table tbody tr {
            height: auto !important;
          }
          .listings-table tbody td {
            padding: 6px 10px !important;
            font-size: 0.82rem !important;
            color: #0f2744;
            border-bottom: 1px solid #e8eef6 !important;
            background: #fff;
            white-space: nowrap !important;
            vertical-align: middle !important;
            height: auto !important;
            line-height: 1.25 !important;
          }
          .listings-table tbody tr:nth-child(even) td {
            background: #f4f8fd !important;
          }
          .listings-table p {
            margin: 0 !important;
            line-height: 1.25 !important;
          }
          [data-testid="stDataFrame"] thead tr th,
          [data-testid="stDataFrame"] [role="columnheader"] {
            background: #003580 !important;
            color: #fff !important;
            font-weight: 700 !important;
          }
          .st-key-export_listings_csv button {
            background: #fff !important;
            color: #003580 !important;
            border: 1px solid #d7e3f4 !important;
            box-shadow: none !important;
            min-width: 36px !important;
            width: 36px !important;
            height: 36px !important;
            padding: 0 !important;
            border-radius: 8px !important;
            font-size: 1.05rem !important;
          }
          .st-key-export_listings_csv button p {
            display: none;
          }
          .st-key-listings_prev button,
          .st-key-listings_next button {
            background: #fff !important;
            color: #0f2744 !important;
            border: 1px solid #d7e3f4 !important;
            box-shadow: none !important;
            min-height: 28px !important;
            height: 28px !important;
            min-width: 28px !important;
            padding: 0 0.5rem !important;
            font-size: 0.9rem !important;
            font-weight: 600 !important;
            border-radius: 6px !important;
          }
          .pager-info {
            text-align: center;
            color: #64748b;
            font-weight: 600;
            font-size: 0.8rem;
            padding-top: 0.28rem;
          }
          @media (max-width: 1100px) {
            .kpi-wrap { grid-template-columns: repeat(2, minmax(0, 1fr)); }
          }
          iframe[height="0"] {
            display: none !important;
            height: 0 !important;
            position: absolute !important;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )
    components.html(
        """
        <script>
        (function () {
          const w = window.parent;
          const doc = w.document;
          const markOpenHint = () => {
            const btn = doc.querySelector('[data-testid="stExpandSidebarButton"]');
            if (!btn) return;
            btn.setAttribute('title', 'Click to open filters');
            btn.setAttribute('aria-label', 'Click to open filters');
          };
          const isCollapsed = () => {
            const el = doc.querySelector('[data-testid="collapsedControl"], [data-testid="stSidebarCollapsedControl"]');
            if (!el) return false;
            const cs = w.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0;
          };
          markOpenHint();
          if (w.__filtersStartCollapsed) return;
          w.__filtersStartCollapsed = true;
          const collapse = () => {
            markOpenHint();
            if (isCollapsed()) return true;
            const sidebar = doc.querySelector('section[data-testid="stSidebar"]');
            if (!sidebar) return false;
            const r = sidebar.getBoundingClientRect();
            const cs = w.getComputedStyle(sidebar);
            const open = r.width > 80 && cs.display !== 'none' && cs.visibility !== 'hidden';
            if (!open) return false;
            const btn = doc.querySelector('[data-testid="stSidebarCollapseButton"]');
            if (btn) {
              btn.click();
              return true;
            }
            return false;
          };
          if (collapse()) return;
          const obs = new MutationObserver(() => {
            if (collapse()) obs.disconnect();
          });
          obs.observe(doc.body, { childList: true, subtree: true });
          w.setTimeout(() => obs.disconnect(), 5000);
        })();
        </script>
        """,
        height=0,
    )


def kpi_row(items: list[tuple[str, str, str]]):
    cells = "".join(
        f'<div class="kpi {cls}"><div class="lbl">{lbl}</div><div class="val">{val}</div></div>'
        for lbl, val, cls in items
    )
    st.markdown(f'<div class="kpi-wrap">{cells}</div>', unsafe_allow_html=True)


st.set_page_config(
    page_title="Booking.com - Data Monitor",
    layout="wide",
    initial_sidebar_state="collapsed",
)
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
    st.markdown(
        """
        <span class="filter-hero">
          <span class="filter-kicker">Refine results</span>
          <span class="filter-title">Filters</span>
        </span>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<span class="filter-section">Search</span>', unsafe_allow_html=True)
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

    st.markdown('<span class="filter-section">Property</span>', unsafe_allow_html=True)
    rooms = sorted(df["room_type"].dropna().astype(str).unique().tolist()) if "room_type" in df.columns else []
    room_type = st.multiselect("Room Type", rooms)

    amenity_opts = unique_amenities(df)
    amenity_sel = st.multiselect("Amenities", amenity_opts)

    sponsored_opt = st.selectbox("Sponsored", ["All", "Yes", "No"])

    st.markdown('<span class="filter-section">Ranges</span>', unsafe_allow_html=True)
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
if amenity_sel:
    view = view[view.apply(lambda r: row_has_amenities(r, amenity_sel), axis=1)]
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

render_pattern_charts(view)

table = pd.DataFrame(
    {
        "Property Name": view.get("title"),
        "Room Type": view.get("room_type"),
        "Bed": view.get("bed_info"),
        "Original Price": view.get("price_original"),
        "Discounted Price": view.get("price_current", view.get("price")),
        "Price Change": view.get("price_change"),
        "Change %": view.get("price_change_pct"),
        "Tax": tax_display(view),
        "Review": view.get("review_word"),
        "Review Score": view.get("review_score"),
        "Reviews": view.get("review_count"),
        "Distance": view.get("distance"),
        "Address": view.get("address"),
        "Postal": view.get("postal_code").map(clean_postal) if "postal_code" in view.columns else None,
        "Available": view.get("available"),
        "Sponsored": view.get("sponsored"),
        "Genius": view.get("genius"),
    }
)
if "Change %" in table.columns:
    _pct = pd.to_numeric(table["Change %"], errors="coerce").round(1)
    table["Change %"] = _pct.map(lambda x: "-" if pd.isna(x) else f"{x:.1f}%")
if "Price Change" in table.columns:
    _chg = pd.to_numeric(table["Price Change"], errors="coerce").round(2)
    table["Price Change"] = _chg.map(lambda x: "-" if pd.isna(x) else f"£{x:.2f}")
if "Review Score" in table.columns:
    _score = pd.to_numeric(table["Review Score"], errors="coerce")
    table["Review Score"] = _score.map(lambda x: "-" if pd.isna(x) else f"{x:.1f}")
if "Reviews" in table.columns:
    table["Reviews"] = table["Reviews"].map(clean_int_count).map(
        lambda x: "-" if pd.isna(x) else str(int(x))
    )
if "Tax" in table.columns:
    table["Tax"] = table["Tax"].fillna("").astype(str)
if "Postal" in table.columns:
    table["Postal"] = table["Postal"].map(clean_postal).fillna("").astype(str)
for flag in ("Available", "Sponsored", "Genius"):
    if flag in table.columns:
        table[flag] = table[flag].fillna(False).astype(bool)

BLANK = {"", "none", "nan", "null", "<na>", "nat"}


def blank_to_dash(val):
    if val is None:
        return "-"
    try:
        if pd.isna(val):
            return "-"
    except (TypeError, ValueError):
        pass
    if isinstance(val, str) and val.strip().lower() in BLANK:
        return "-"
    return val


for col in table.columns:
    if col in ("Available", "Sponsored", "Genius"):
        continue
    table[col] = table[col].map(blank_to_dash)


def _chg_style(val):
    if val is None or val == "-":
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    try:
        n = float(str(val).replace("£", "").replace("%", "").replace(",", "").strip())
    except ValueError:
        return ""
    if n > 0:
        return "color: #c1121f; font-weight: 600"
    if n < 0:
        return "color: #1b7f5a; font-weight: 600"
    return ""


filter_key = (
    city,
    check_in,
    name_q,
    tuple(room_type),
    tuple(amenity_sel),
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
show = page_table.copy()
for flag in ("Available", "Sponsored", "Genius"):
    if flag in show.columns:
        show[flag] = show[flag].map(lambda x: "Yes" if bool(x) else "No")
styler = show.style
if style_cols:
    styler = styler.map(_chg_style, subset=style_cols)
styler = styler.set_table_styles(
    [
        {
            "selector": "th",
            "props": [
                ("background-color", "#003580"),
                ("color", "#ffffff"),
                ("font-weight", "700"),
                ("border", "0"),
                ("white-space", "nowrap"),
                ("padding", "8px 10px"),
            ],
        },
        {
            "selector": "td",
            "props": [
                ("white-space", "nowrap"),
                ("padding", "6px 10px"),
                ("vertical-align", "middle"),
                ("line-height", "1.25"),
            ],
        },
    ]
).hide(axis="index").set_properties(**{"white-space": "nowrap", "vertical-align": "middle"})
shown_from = 0 if total_rows == 0 else start + 1
shown_to = min(start + PAGE_SIZE, total_rows)
csv_bytes = table.to_csv(index=False).encode("utf-8")
with st.container(border=True):
    title_col, export_col = st.columns([12, 1])
    with title_col:
        st.markdown('<div class="table-title">Listings</div>', unsafe_allow_html=True)
    with export_col:
        st.download_button(
            label="Export",
            data=csv_bytes,
            file_name="listings_filtered.csv",
            mime="text/csv",
            help="Export CSV",
            icon=":material/download:",
            key="export_listings_csv",
        )
    st.markdown(
        f'<div class="listings-table">{styler.to_html()}</div>',
        unsafe_allow_html=True,
    )
    _, prev_col, info_col, next_col, _ = st.columns([3.5, 0.5, 2.2, 0.5, 3.5])
    with prev_col:
        if st.button("<", disabled=page <= 1, key="listings_prev"):
            st.session_state.listings_page = page - 1
            st.rerun()
    with info_col:
        st.markdown(
            f'<div class="pager-info">{shown_from}–{shown_to} of {total_rows:,}</div>',
            unsafe_allow_html=True,
        )
    with next_col:
        if st.button(">", disabled=page >= total_pages, key="listings_next"):
            st.session_state.listings_page = page + 1
            st.rerun()
