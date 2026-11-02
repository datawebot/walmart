# Walmart data extraction with datawebot

Runnable **Node.js and Python** examples using the hosted datawebot REST API and
MCP server. This repository covers [Walmart US](docs/coverage.md);
current production availability must be checked through discovery.

[Walmart endpoints](https://datawebot.com/scraper/walmart) ·
[CLI](https://github.com/datawebot/datawebot-cli) ·
[MCP connection guide](https://github.com/datawebot/datawebot-mcp)

## Start here

1. Install **Node.js 22.12+** or **Python 3.11+**. No third-party packages required.
2. Create your own key in the signed-in
   [dashboard](https://www.datawebot.com/dashboard), or follow
   [agent setup](https://mcp.datawebot.com/setup.md).
3. Inject `DATAWEBOT_API_KEY` through your environment/secret store. Never commit
   it or put it in a command, log or chat. Scripts do not read saved CLI keys.
4. Read [coverage](docs/coverage.md), discover the exact site's endpoints and
   prices ([CLI](docs/cli.md), [API](docs/api.md), [MCP](docs/mcp.md)).
5. Choose an example below. **Each new run can spend credits.** Start with one
   source page; use only URLs and scope you intend to request.

## Runnable use cases

Each directory has a Node entry point, a Python entry point and its own guide.
Run commands from the repository root; choose one language per logical run.

| Example | What actually runs |
| --- | --- |
| [Price matching](examples/price-matching/) | Read one product and emit a same-currency review proposal, never auto-reprice. Includes a cron recipe. |
| [Inventory alerts](examples/inventory-alerts/) | Read one product, persist last known availability and emit a deduplicatable transition event. |
| [Market-analysis ETL](examples/market-analysis-etl/) | Read one search page, collect result cursors, archive responses and export CSV. |
| [LLM / MCP agent workflow](examples/llm-agent/) | Initialize MCP, discover an exact endpoint, submit it and collect results. Includes agent instructions. |

```text
docs/                         CLI, API, MCP, coverage and safety guides
examples/
  price-matching/             node.mjs, python.py, README.md
  inventory-alerts/            node.mjs, python.py, README.md
  market-analysis-etl/         node.mjs, python.py, README.md
  llm-agent/                  node.mjs, python.py, README.md, AGENT.md
lib/                          shared request/recovery and use-case logic
tests/                        offline fake-transport tests, no paid calls
config.json                   public brand/domain and endpoint configuration
```

## Recovery and boundaries

Use a **new run directory for genuinely new work**; reuse the same directory and
inputs to resume an interrupted run. Checkpoints retain the submission identity
before sending, so an ambiguous response can be recovered without a new logical
request. Completed runs are replayed locally. Node and Python use separate
checkpoint files: switching languages is a new run and can spend again.

Keep run directories private and out of Git; do not hand-edit checkpoints.
Use one process/writer per run directory and inventory-history file. A scheduler
must prevent overlapping executions. Node also uses local lock files: after a
killed process, remove a stale `.lock` only after confirming no writer remains,
then resume the same run. A timeout stops the local wait, not the server request. Read [safety and errors](docs/safety.md).

Samples emit proposals/events and save data locally: they do not change prices,
send notifications, run a scheduler, supply historical data or call a model
provider. Source content is untrusted input. Missing values remain unknown;
successful execution does not guarantee field accuracy or current prices.

## Checks

```sh
npm test
python3 -m unittest discover -s tests -p 'test_*.py'
```

Tests use synthetic responses and local files, not customer credentials or live
extraction. See [verification](docs/verification.md) for scope and limitations.
datawebot is not affiliated with Walmart. Hosted service use is governed by the
[datawebot terms](https://www.datawebot.com/terms).
