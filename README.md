# Booking.com listings — Streamlit dashboard

Monitor for search listings stored in DuckDB. It does **not** scrape Booking.com; it only reads the warehouse filled by `../modal` (or a local `python listings.py` run).

## How it works

1. `app.py` opens DuckDB **read-only**.
2. It loads `search_listings`, parses price / review / distance, and keeps **one row per property URL + check-in + room type** (latest capture).
3. Sidebar filters cut that unique set. KPI cards and the table use the filtered rows.
4. Price change vs the previous check-in day is joined when two days of data exist.
5. The table is paginated (10 rows). CSV download is the full filtered set, not just the current page.

## Find the database

The app reads **`booking.duckdb` in this folder** (shipped with the deploy). Override with `DUCKDB_PATH` if needed. It also falls back to `../booking.duckdb` and `../modal/booking.duckdb`.

After a Modal scrape, copy `/data/booking.duckdb` into this folder (or set `DUCKDB_PATH`) so the hosted UI sees new rows.

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
| Streamlit Community Cloud | Put `booking.duckdb` in this folder and deploy the folder; refresh the file when data changes |
| Object storage | Scraper uploads `booking.duckdb`; app downloads it at startup into `DUCKDB_PATH` |

Do not point the dashboard at the same file the scraper is actively writing. Prefer a snapshot copy for the UI.

## Filters and table

**Filters:** City, Check In (date picker), property name (clear with ×), room type, sponsored, review score, price (£), distance (km).

**Cards:** unique properties, available, not available, average price, average change %.

**Table:** property, room, bed, prices, tax, reviews, distance, available, sponsored, Genius.
