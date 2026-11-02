# Walmart examples with datawebot

Small, **self-contained Node.js and Python scripts** for the hosted datawebot API
and MCP server. Open an example and read it top to bottom: the request payload,
polling and use-case logic are all in that file. No local imports, dispatcher,
shared client or config file.

[Walmart endpoints](https://datawebot.com/scraper/walmart) ·
[Coverage](docs/coverage.md) · [CLI](docs/cli.md) ·
[API](docs/api.md) · [MCP](docs/mcp.md)

## Choose an example

| Directory | What the code shows |
| --- | --- |
| [price-matching](examples/price-matching/) | Fetch one product, compare known same-currency prices, print a human-review proposal. |
| [inventory-alerts](examples/inventory-alerts/) | Fetch one product, compare with a local previous observation, print a stock-transition event. |
| [market-analysis-etl](examples/market-analysis-etl/) | Fetch one search page, follow result cursors, save raw JSON and CSV. |
| [llm-agent](examples/llm-agent/) | Initialize MCP, discover an endpoint, submit one search page and collect its results. |

Each directory contains `python.py`, `node.mjs` and a short README.
Copy either script out of the repository if you like: it has no repository-local
runtime dependencies. Requires Python 3.11+ or Node.js 22.12+.

## Setup

1. Create your own key in the [dashboard](https://www.datawebot.com/dashboard),
   or follow [agent setup](https://mcp.datawebot.com/setup.md).
2. Supply `DATAWEBOT_API_KEY` through your environment/secret store. Never commit
   it, log it, or paste it in chat.
3. Discover the exact site's availability and price before submitting work.
4. Run one example with an intended URL and a **stable request key**:

```sh
python3 examples/price-matching/python.py "$PRODUCT_URL" 49.99 USD price-check-001
# Node alternative:
node examples/price-matching/node.mjs "$PRODUCT_URL" 49.99 USD price-check-001
```

**New keys can spend credits.** A product example has a one-credit ceiling; a
search example has a five-credit ceiling. The service refuses a higher quote.
For an interrupted/uncertain submission, use the **same URL and request key**;
the API returns the same request rather than starting another paid one.
Use a new key only for an intentional new observation. Keys are global to your
account: do not reuse a key for different inputs or between REST and MCP recipes.

The scripts print JSON proposals/events/results; ETL also writes files. They
do not change prices, send notifications, schedule jobs or call an LLM provider.
Keep inventory runs ordered and single-writer. API snapshots are not guaranteed
current prices or checkout inventory. See [safety](docs/safety.md).


## Tests

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

The offline suite exercises both Python and Node scripts using mocked HTTP;
both runtimes must be installed to run the full suite.
See [verification](docs/verification.md). No paid extraction is part of tests.
datawebot is not affiliated with Walmart; hosted use is governed by the
[terms](https://www.datawebot.com/terms).
