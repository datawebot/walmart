# REST API setup and usage

### Authentication

Create your own key through the signed-in
[dashboard](https://www.datawebot.com/dashboard) API keys section, or follow the
[agent setup guide](https://mcp.datawebot.com/setup.md). Inject it as
`DATAWEBOT_API_KEY` using your shell/runner's secret store. Do not put a literal
key in source, command history, logs or cron entries; do not enable shell tracing.

Examples require curl; JSON files are private local artifacts. Use HTTPS and the
documented API origin only; do not forward credentials to arbitrary hosts.

### Discover the exact site

```sh
curl --fail-with-body --silent --show-error --get \
  'https://api.datawebot.com/v1/job-blueprints' \
  -H "Authorization: Bearer $DATAWEBOT_API_KEY" \
  --data-urlencode 'site=walmart.com' > work/blueprints.json
```

Create `work/` with owner-only permissions (`umask 077; mkdir -p work`) if you skipped the CLI.
Inspect contracts, prices and availability; follow `next_cursor` with identical
filters if present. The reference below uses the current request router, which
also backs the MCP tools.

### Submit one exact endpoint

Save this as `work/request.json`. Adjust the query URL and key before your
first submission. The example budget is a **maximum of 5 credits**, not a
promise of price or authorization; discovery determines the current price.

```json
{
  "capability": "endpoint",
  "endpoint": "walmart.search",
  "url": "https://www.walmart.com/search?q=shoes",
  "format": "structured",
  "max_credits": 5,
  "request_key": "walmart-search-example-001"
}
```

```sh
curl --fail-with-body --silent --show-error \
  'https://api.datawebot.com/v1/requests' \
  -H "Authorization: Bearer $DATAWEBOT_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @work/request.json > work/answer.json
```

`capability: "endpoint"` runs only the named endpoint: **no shopping-search
fallback**. Keep the exact request file and `request_key` until the outcome is
known. After an ambiguous POST, replay the identical payload/key, not a new key.

### Wait and collect

Inspect `work/answer.json`:

- `status: "working"`: save `request_id`, wait at least the returned
  `check_after_seconds`, then read the request again. Bound waits (for example,
  ten minutes) and retain the ID for later recovery.
- `status: "done"`: consume the returned data, while checking errors/warnings.
- `status: "failed"` or HTTP error: report it; never reinterpret failure as an
  empty successful dataset.

```sh
# Replace REQUEST_ID with the saved request_id. This read submits no new work.
curl --fail-with-body --silent --show-error \
  'https://api.datawebot.com/v1/requests/REQUEST_ID' \
  -H "Authorization: Bearer $DATAWEBOT_API_KEY" > work/answer.json

# If next_cursor is present, read each page using its opaque cursor.
curl --fail-with-body --silent --show-error --get \
  'https://api.datawebot.com/v1/requests/REQUEST_ID' \
  -H "Authorization: Bearer $DATAWEBOT_API_KEY" \
  --data-urlencode 'cursor=NEXT_CURSOR_FROM_PREVIOUS_ANSWER' > work/next-page.json
```

Exact-endpoint answers contain `products`, including product-detail endpoints
(usually one item). The separate `capability: "product"` intent API returns
`product` instead. Do not confuse the two shapes.
Keep `request_id`, `meta.as_of`, source URLs and all page/error metadata. Save
each page before following `next_cursor` until null. Records can contain null
prices, currency, stock, ratings or IDs: unknown is **not zero**.

For a product-detail request, use a new saved key, set `endpoint` to the discovered
product endpoint and `url` to one observed product URL. Recheck the budget first.
Use `format: "raw"` if you also need the endpoint's own records; it is not HTML.

### Legacy Jobs API used by CLI 0.1.2

Do not mix router request IDs with Job IDs. Direct Jobs integrations use:

1. `GET /v1/job-blueprints`: read the exact ID, version and schemas.
2. `POST /v1/jobs`: `blueprint_id`, `blueprint_version`, `url`,
   `parameters`, with a persisted `Idempotency-Key` header.
3. `GET /v1/jobs/{id}`: unfinished states are `PENDING`/`INITIATED`;
   follow ETA, `poll_after_seconds` and `Retry-After`.
4. `GET /v1/jobs/{id}/results?page_size=100`: follow `next_cursor` with
   the same page size. Results default to raw `records[].data`; structured
   formats are available only where the live contract supports them.

`COMPLETED` describes execution, not independently verified field accuracy.
`PARTIAL_COMPLETE` needs explicit review. `FAILED` is terminal;
`UNAVAILABLE` means maintenance, not a reason to resubmit. Results expire after
seven days, so export required data to your own storage. See the
[OpenAPI reference](https://www.datawebot.com/openapi.json) for full schemas.
