# MCP setup and usage

The server is hosted at **`https://mcp.datawebot.com/mcp`**, using Streamable HTTP.

For Claude Code:

```sh
claude mcp add --transport http datawebot https://mcp.datawebot.com/mcp
```

Then use `/mcp` to connect and approve email sign-in. Other clients can use:

```json
{
  "mcpServers": {
    "datawebot": {
      "url": "https://mcp.datawebot.com/mcp"
    }
  }
}
```

Use a client that supports remote HTTP MCP and OAuth, or configure its secret
store to supply an `Authorization: Bearer` header. See the
[client-specific connection guide](https://github.com/datawebot/datawebot-mcp).
Never put credentials in tool arguments.

Tool calls below are examples for a connected MCP client, **not shell commands**:

1. Call `discover {}` and follow its current schemas.
2. Call `find_endpoints {"site":"walmart.com"}`. Inspect IDs, prices,
   allowed inputs and availability.
3. Call `submit_job` with the discovered search endpoint, for example:

```json
{
  "endpoint": "walmart.search",
  "url": "https://www.walmart.com/search?q=shoes",
  "format": "structured",
  "max_credits": 5,
  "request_key": "walmart-mcp-example-001"
}
```

4. If pending, persist `structuredContent.data.request_id`. While its `status`
   is `working`, wait `check_after_seconds`, then call
   `get_results {"request_id":"SAVED_REQUEST_ID"}`.
5. Follow `next_cursor` with
   `get_results {"request_id":"SAVED_REQUEST_ID","cursor":"RETURNED_CURSOR"}`.
   Select one relevant product and submit its endpoint with a new key only if
   the user authorized that extra work.

Check MCP `isError` and structured tool output, not just HTTP status.
Discovery, endpoint lists and answers are wrapped in `structuredContent.data`,
with metadata alongside `data`. A completed answer may have `products` without
`status` or `request_id`; do not require a REST-style envelope. The
[runnable MCP clients](../examples/llm-agent/) show the complete protocol. If a tool
was added after the session started, `discover` documents `run_tool`.

For exploratory shopping, use `search_products` with `site: "walmart.com"`,
`country: "US"` and a small `limit`, then `get_product` on an observed URL or
product ID. This intent workflow may use a shopping-search fallback; it is not
proof that the exact source endpoint or another country is verified.
