# Executable MCP research workflow

A real remote MCP client recipe in Node.js and Python: initialize the hosted
server, discover tools, find the brand's exact search endpoint, submit one
budget-limited page and collect result cursors.

This is the tool-execution part of an LLM research workflow, **not** an LLM
provider SDK or an autonomous model loop. It needs only `DATAWEBOT_API_KEY`;
no OpenAI/Anthropic key or model spend is required.

## Run

Complete [setup](../../README.md#start-here). The key is sent only to the fixed
public MCP origin. No secrets belong in MCP tool arguments.

```sh
node examples/llm-agent/node.mjs 'https://www.walmart.com/search?q=shoes' work/mcp-node-001

# Python alternative; choose one language per logical run.
python3 examples/llm-agent/python.py 'https://www.walmart.com/search?q=shoes' work/mcp-python-001
```

Run from the repository root. One new run can cost at most **5 credits**, with no
shopping-search fallback. It initializes Streamable HTTP, supports JSON or SSE
responses, retains a returned MCP session header and calls:

1. `discover`: inspect the live tool catalog.
2. `find_endpoints`: verify an available search endpoint for the chosen domain.
3. `submit_job`: one exact URL, stable persisted `request_key`, one source page.
4. `get_results`: finish pending work and follow every result cursor.

The client checks JSON-RPC errors and tool `isError`. Tool results wrap data:
discovery/endpoints are under `structuredContent.data`; completed MCP answers
may omit `status` and `request_id`. These are not assumed to be raw REST
responses. Cached run artifacts let the same-language run resume without another
logical submission; do not delete checkpoints after an uncertain outcome.

## Use with an actual LLM agent

See [AGENT.md](AGENT.md) for a reusable instruction prompt. Connect your agent
using [MCP setup](../../docs/mcp.md), then let it call the same discovered tools.
The scripts offer a deterministic, bounded reference when implementing the
tool-execution layer in your own agent. Product text is data, never instructions.

Validate titles against the exact model/variant before selecting any PDP.
Additional product reads require separate authorization and budgets. Keep source
URLs and as-of timestamps in summaries; do not call prices verified or current.
