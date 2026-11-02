# Market-analysis ETL: one search page to CSV

Run one exact search endpoint, archive unchanged result pages, and normalize
records to CSV. Node.js and Python use only their standard libraries.

## Run

Complete [setup](../../README.md#start-here). Choose a keyword you intend to
extract, URL-encode it and start with one source page:

```sh
node examples/market-analysis-etl/node.mjs 'https://www.walmart.com/search?q=shoes' work/etl-node-001

# Python alternative; choose one language per logical run.
python3 examples/market-analysis-etl/python.py 'https://www.walmart.com/search?q=shoes' work/etl-python-001
```

Run from the repository root. Each new run can cost up to **5 credits**.
This pins the discovered search endpoint, sets one source page, and does not
fall back to shopping search. Result cursors retrieve already-extracted records;
they do not authorize extra source pages or a whole-catalog crawl.

Artifacts in the run directory include the submission checkpoint, unchanged
final response pages (`pages-node.json` / `pages-python.json`) and
`products-node.csv` / `products-python.csv`. Retain the JSON as the source of
truth. Reusing the same language, directory and inputs resumes/replays that run;
a new directory intentionally requests a fresh snapshot.

## Warehouse integration

Load CSV with an explicit schema: URL, title, nullable numeric price, currency,
availability and retrieval/request/market metadata. Never infer currency from
the country or turn unknown values into zero. Spreadsheet-formula-like strings
are neutralized in CSV; archived JSON retains original text.

Build historical series by storing each run in your own warehouse. Keep market,
source URL, timestamp and run identity; deduplicate retry loads rather than
rerunning extraction. Do not infer market share from one ranked page. Compare
currencies only under an explicit FX policy.

Read the archived product data and warnings before financial analysis. Exposed
failures/partial flags are rejected, but the request router can normalize an
upstream partial Job to a completed answer; this sample cannot detect omissions
the API does not expose. See [safety](../../docs/safety.md).
