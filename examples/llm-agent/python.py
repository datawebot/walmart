"""Run a one-page MCP research workflow, without an LLM SDK or hidden client."""
import json
import math
import os
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

if len(sys.argv) != 3:
    sys.exit("Usage: python.py SEARCH_URL REQUEST_KEY")
url, request_key = sys.argv[1:]
target = urlsplit(url)
host = (target.hostname or "").removeprefix("www.")
allowed_hosts = ["walmart.com"]
query = parse_qs(target.query, keep_blank_values=True)
if target.scheme != "https" or host not in allowed_hosts or target.username or target.password or target.port or target.fragment or target.hostname != "www.walmart.com":
    sys.exit("Use a public HTTPS Walmart URL.")
if target.path != "/search" or set(query) != {"q"} or len(query["q"]) != 1 or not 0 < len(query["q"][0].strip()) <= 200 or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_key):
    sys.exit("Use one keyword, no filters, and a stable 8-128 character request key.")
deadline = time.monotonic() + 600
sequence = 0

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

# The hosted server returns JSON over stateless HTTP. Use an MCP SDK for SSE/session transports.
def rpc(method, params):
    global sequence
    sequence += 1
    notification = method == "notifications/initialized"
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if not notification:
        body["id"] = sequence
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        sys.exit("Timed out; retry with the SAME request key. Server work is not cancelled.")
    req = Request("https://mcp.datawebot.com/mcp", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["DATAWEBOT_API_KEY"],
                 "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                 "MCP-Protocol-Version": "2025-03-26"})
    try:
        with build_opener(NoRedirect()).open(req, timeout=min(30, remaining)) as response:
            if notification:
                return None
            if "application/json" not in response.headers.get("Content-Type", ""):
                sys.exit("This example expects the hosted JSON transport; use an MCP SDK for SSE.")
            message = json.load(response)
    except HTTPError as error:
        sys.exit("MCP HTTP %s; wait Retry-After=%s before retrying with the SAME key." % (error.code, error.headers.get("Retry-After", "not supplied")))
    if message.get("id") != sequence or message.get("error") or "result" not in message:
        sys.exit("MCP JSON-RPC failure; no source results to use.")
    result = message["result"]
    if result.get("isError"):
        sys.exit("MCP tool failed; do not treat this as an empty result.")
    return result

# 1. Connect, then inspect the live tools and exact endpoint.
rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
    "clientInfo": {"name": "walmart-python-example", "version": "1.0"}})
rpc("notifications/initialized", {})
catalog = rpc("tools/call", {"name": "discover", "arguments": {}})["structuredContent"]["data"]
if not {"find_endpoints", "submit_job", "get_results"} <= {tool["name"] for tool in catalog["tools"]}:
    sys.exit("Required tools absent; follow the current discovery schema.")
endpoints = rpc("tools/call", {"name": "find_endpoints", "arguments": {"site": host, "type": "search"}})["structuredContent"]["data"]["endpoints"]
if not any(e["endpoint"] == "walmart.search" and e.get("available") is True for e in endpoints):
    sys.exit("Search endpoint unavailable; no shopping fallback.")

# 2. Submit one page. Keep this request key unchanged after a timeout or lost response.
answer = rpc("tools/call", {"name": "submit_job", "arguments": {
    "endpoint": "walmart.search", "url": url, "max_pages": 1,
    "format": "structured", "max_credits": 5, "request_key": request_key}})["structuredContent"]

# 3. Wait for pending answers and follow result cursors, not extra source pages.
pages, seen, request_id = [], set(), None
while True:
    data = answer["data"]
    if data.get("request_id"):
        if not re.fullmatch(r"[a-fA-F0-9-]{36}", data["request_id"]) or (request_id and request_id != data["request_id"]):
            sys.exit("Invalid/changed request ID.")
        request_id = data["request_id"]
    if data.get("status") == "working":
        delay = max(2, data.get("check_after_seconds", 5))
        if not request_id or not math.isfinite(delay) or time.monotonic() + delay >= deadline:
            sys.exit("Pending beyond this sample's wait; retry with the SAME key.")
        time.sleep(delay)
        arguments = {"request_id": request_id}
    else:
        if data.get("status") not in (None, "done") or data.get("error") or data.get("partial") or data.get("errors") or not isinstance(data.get("products"), list):
            sys.exit("Failed/partial or malformed answer.")
        pages.append(answer)  # Keep the MCP data/meta wrapper, including as-of timestamps.
        cursor = data.get("next_cursor")
        if cursor is None:
            break
        if not request_id or not isinstance(cursor, str) or not cursor or cursor in seen:
            sys.exit("Missing request ID or invalid/repeated cursor.")
        seen.add(cursor)
        arguments = {"request_id": request_id, "cursor": cursor}
    answer = rpc("tools/call", {"name": "get_results", "arguments": arguments})["structuredContent"]

print(json.dumps(pages, indent=2))  # Source text is data, never agent instructions.
