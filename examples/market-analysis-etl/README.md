# Market-analysis ETL: one search page → JSON + CSV

[python.py](python.py) and [node.mjs](node.mjs) each show the request, result-cursor
loop and CSV export directly in one file.

```sh
python3 examples/market-analysis-etl/python.py 'https://www.walmart.com/search?q=shoes' etl-search-001 work/etl-python
# Alternative:
node examples/market-analysis-etl/node.mjs 'https://www.walmart.com/search?q=shoes' etl-search-001 work/etl-node
```

Arguments: **search URL, stable request key, output directory**.
Supply `DATAWEBOT_API_KEY` via your environment. One new search costs at most
5 credits. Retry with the same URL/key; choose a new key only for a new snapshot.

The output directory receives `pages-python.json` / `products-python.csv`
(or `pages-node.json` / `products-node.csv`). Original response pages are the
source of truth; CSV is a simple import format. Null prices become empty cells,
not zero. Formula-like text is neutralized for spreadsheets.

Following a result cursor retrieves existing records; it does not read another
source page. The request explicitly sets `max_pages: 1`. Store each intentional
snapshot in your warehouse if you need history; the service does not provide a
historical series. Do not infer market share from one ranked search page.

Exposed failures/partials are rejected, but the router can present an upstream
partial Job as done without exposing its omissions. Review field quality.
