# Booking.com listings — Streamlit dashboard

Monitor for search listings stored in DuckDB. It does **not** scrape Booking.com; it only reads the warehouse filled by `../modal` (or a local `python listings.py` run).

## How it works

1. `app.py` opens DuckDB **read-only**.
2. It loads `search_listings`, parses price / review / distance, and keeps **one row per property URL + check-in + room type** (latest capture).
3. Sidebar filters cut that unique set. KPI cards and the table use the filtered rows.
4. Price change vs the previous check-in day is joined when two days of data exist.
5. The table is paginated (10 rows). CSV download is the full filtered set, not just the current page.

## Find the database

`resolve_db()` checks, in order:

1. Environment variable `DUCKDB_PATH`
2. `../booking.duckdb` (project root)
3. `../modal/booking.duckdb`

Copy `.env.example` to `.env` if you want a custom path. The file is currently about 4 MB.

## Run locally

```powershell
cd c:\Users\jeeva\Documents\scrapper\dashboard
pip install -r requirements.txt
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501).

## Hosting

DuckDB is an embedded file. The hosted app must be able to **open that file**.

| Approach | How the app reads DuckDB |
| --- | --- |
| Same machine / Modal Volume | Mount the volume and set `DUCKDB_PATH=/data/booking.duckdb` |
| Streamlit Community Cloud | Commit or download a snapshot; Community Cloud has no persistent disk |
| Object storage | Scraper uploads `booking.duckdb`; app downloads it at startup into `DUCKDB_PATH` |

Do not point the dashboard at the same file the scraper is actively writing. Prefer a snapshot copy for the UI.

## Filters and table

**Filters:** City, Check In (date picker), property name (clear with ×), room type, sponsored, review score, price (£), distance (km).

**Cards:** unique properties, available, not available, average price, average change %.

**Table:** property, room, bed, prices, tax, reviews, distance, available, sponsored, Genius.
