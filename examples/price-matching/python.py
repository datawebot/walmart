"""Read one product and propose a lower same-currency price for human review."""
import json
import math
import os
import re
import sys
import time
from decimal import Decimal
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

if len(sys.argv) != 5:
    sys.exit("Usage: python.py PRODUCT_URL OUR_PRICE CURRENCY REQUEST_KEY")
os.umask(0o077)
url, our_price, currency, request_key = sys.argv[1:]
our_price = Decimal(our_price)
if not our_price.is_finite() or our_price < 0 or not re.fullmatch(r"[A-Z]{3}", currency):
    sys.exit("Use a nonnegative decimal price and an uppercase ISO currency code.")
target = urlsplit(url)
host = (target.hostname or "").removeprefix("www.")
allowed_hosts = ["walmart.com"]
if target.scheme != "https" or host not in allowed_hosts or target.username or target.password or target.port or target.fragment or target.hostname != "www.walmart.com":
    sys.exit("Use an HTTPS Walmart URL without credentials, ports or fragments.")
match = re.fullmatch(r"/ip/[A-Za-z0-9_-]{1,400}/([1-9][0-9]{0,19})", target.path)
identity = match[1] if match and not target.query else None
if not identity or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_key):
    sys.exit("Use the documented URL shape and a stable 8-128 character request key.")

# Keep this key on retries. The server returns the same request instead of billing a new one.
payload = {"capability": "endpoint", "endpoint": "walmart.product_details",
           "url": url, "max_pages": 1, "format": "structured",
           "max_credits": 1, "request_key": request_key}
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

# Compare this exact listing, never search rank or an inferred currency.
if len(answer["products"]) != 1:
    sys.exit("Expected one product; no price proposal.")
product = answer["products"][0]
observed = urlsplit(product.get("url") or "")
if observed.scheme != "https" or observed.username or observed.password or observed.hostname != target.hostname or not observed.path.rstrip("/").endswith("/" + identity):
    sys.exit("Returned product does not match the requested listing.")
price = product.get("price") or {}
amount = price.get("amount")
as_of = answer.get("meta", {}).get("as_of")
try:
    if not isinstance(as_of, str) or datetime.fromisoformat(as_of.replace("Z", "+00:00")).tzinfo is None:
        as_of = None
except ValueError:
    as_of = None
known_price = type(amount) in (int, float) and math.isfinite(amount) and amount >= 0
comparable = known_price and price.get("currency") == currency and bool(as_of)
proposal = {
    "action": "review_price_match" if comparable and Decimal(str(amount)) < our_price else "no_proposal",
    "our_price": str(our_price), "observed_price": amount,
    "currency": price.get("currency"), "source": product.get("url"), "as_of": as_of,
    "request_id": answer["request_id"],
    "note": "Review model, variant, seller, shipping and taxes; this does not change a price.",
}
print(json.dumps(proposal, indent=2))
