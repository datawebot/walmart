"""Read one search page, collect its result cursors and write CSV."""
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import csv
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

if len(sys.argv) != 4:
    sys.exit("Usage: python.py SEARCH_URL REQUEST_KEY OUTPUT_DIR")
os.umask(0o077)
url, request_key, output_dir = sys.argv[1:]
output_dir = Path(output_dir)
target = urlsplit(url)
host = (target.hostname or "").removeprefix("www.")
allowed_hosts = ["walmart.com"]
if target.scheme != "https" or host not in allowed_hosts or target.username or target.password or target.port or target.fragment or target.hostname != "www.walmart.com":
    sys.exit("Use an HTTPS Walmart URL without credentials, ports or fragments.")
query = parse_qs(target.query, keep_blank_values=True)
identity = target.path == "/search" and set(query) == {"q"} and len(query["q"]) == 1 and 0 < len(query["q"][0].strip()) <= 200
if not identity or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_key):
    sys.exit("Use the documented URL shape and a stable 8-128 character request key.")

# Keep this key on retries. The server returns the same request instead of billing a new one.
payload = {"capability": "endpoint", "endpoint": "walmart.search",
           "url": url, "max_pages": 1, "format": "structured",
           "max_credits": 5, "request_key": request_key}
deadline = time.monotonic() + 600

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the API key to a redirect destination.

def request(path="", body=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        sys.exit("Wait timed out; rerun with the SAME request key. The server work is not cancelled.")
    req = Request("https://api.datawebot.com/v1/requests" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + os.environ["DATAWEBOT_API_KEY"], "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(req, timeout=min(30, remaining)) as response:
            return json.load(response)
    except HTTPError as error:
        sys.exit("API HTTP %s; wait Retry-After=%s before retrying with the SAME key." % (error.code, error.headers.get("Retry-After", "not supplied")))

# Submit once, then make read-only calls until the answer is ready.
answer = request(body=payload)
request_id = answer.get("request_id")
if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", request_id):
    sys.exit("Missing/invalid request ID; rerun with the SAME key.")
while answer.get("status") == "working":
    if answer.get("request_id") != request_id:
        sys.exit("Request ID changed; no downstream changes.")
    delay = max(2, answer.get("check_after_seconds", 5))
    if not math.isfinite(delay) or time.monotonic() + delay >= deadline:
        sys.exit("Wait timed out; rerun with the SAME key.")
    time.sleep(delay)
    answer = request("/" + request_id)
if answer.get("request_id") != request_id or answer.get("status") != "done" or answer.get("error") or answer.get("partial") or answer.get("errors") or answer.get("meta", {}).get("partial") or answer.get("meta", {}).get("errors"):
    sys.exit("Request failed or is partial; no downstream changes.")
if not isinstance(answer.get("products"), list) or any(not isinstance(p, dict) for p in answer["products"]):
    sys.exit("Malformed products; no downstream changes.")

# Collect existing result pages, not additional source pages.
pages, seen = [], set()
request_id = answer["request_id"]
while True:
    if answer.get("request_id") != request_id or answer.get("status") != "done" or answer.get("error") or answer.get("partial") or answer.get("errors") or answer.get("meta", {}).get("partial") or answer.get("meta", {}).get("errors"):
        sys.exit("Failed or partial result page; CSV was not written.")
    if not isinstance(answer.get("products"), list) or any(not isinstance(p, dict) for p in answer["products"]):
        sys.exit("Malformed products; CSV was not written.")
    pages.append(answer)
    cursor = answer.get("next_cursor")
    if cursor is None:
        break
    if not isinstance(cursor, str) or not cursor or cursor in seen:
        sys.exit("Invalid/repeated cursor; CSV was not written.")
    seen.add(cursor)
    answer = request("/" + request_id + "?" + urlencode({"cursor": cursor}))

output_dir.mkdir(parents=True, exist_ok=True)
(output_dir / "pages-python.json").write_text(json.dumps(pages, indent=2))

def safe_cell(value):
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text

# CSV is disposable; original responses above are the source of truth.
csv_file = output_dir / "products-python.csv"
with csv_file.with_suffix(".tmp").open("w", newline="") as out:
    writer = csv.writer(out)
    writer.writerow(["url", "title", "price", "currency", "availability", "as_of", "request_id", "country"])
    for page in pages:
        for product in page["products"]:
            price, meta = product.get("price") or {}, page.get("meta") or {}
            if not isinstance(price, dict) or (price.get("amount") is not None and (type(price["amount"]) not in (int, float) or not math.isfinite(price["amount"]) or price["amount"] < 0)):
                sys.exit("Malformed price; use the archived JSON to investigate.")
            writer.writerow([safe_cell(v) for v in [product.get("url"), product.get("title"), price.get("amount"),
                price.get("currency"), product.get("availability"), meta.get("as_of"), page["request_id"], meta.get("country")]])
csv_file.with_suffix(".tmp").replace(csv_file)
print("Saved pages-python.json and products-python.csv")
