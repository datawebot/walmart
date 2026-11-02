"""Read one product and report an explicit stock transition; never send a notification."""
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

if len(sys.argv) != 4:
    sys.exit("Usage: python.py PRODUCT_URL HISTORY_FILE REQUEST_KEY")
os.umask(0o077)
url, history_file, request_key = sys.argv[1:]
history_file = Path(history_file)
history = json.loads(history_file.read_text()) if history_file.exists() else None
target = urlsplit(url)
host = (target.hostname or "").removeprefix("www.")
allowed_hosts = ["walmart.com"]
if target.scheme != "https" or host not in allowed_hosts or target.username or target.password or target.port or target.fragment or target.hostname != "www.walmart.com":
    sys.exit("Use an HTTPS Walmart URL without credentials, ports or fragments.")
match = re.fullmatch(r"/ip/[A-Za-z0-9_-]{1,400}/([1-9][0-9]{0,19})", target.path)
identity = match[1] if match and not target.query else None
if not identity or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_key):
    sys.exit("Use the documented URL shape and a stable 8-128 character request key.")
if history and history.get("url") != url:
    sys.exit("History belongs to another product; choose a different history file.")

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

# Unknown stock must not overwrite the last known state.
if len(answer["products"]) != 1:
    sys.exit("Expected one product; history was not changed.")
product = answer["products"][0]
observed = urlsplit(product.get("url") or "")
if observed.scheme != "https" or observed.username or observed.password or observed.hostname != target.hostname or not observed.path.rstrip("/").endswith("/" + identity):
    sys.exit("Returned product does not match the requested listing.")
availability = product.get("availability")
as_of = answer.get("meta", {}).get("as_of")
if availability not in ("in_stock", "out_of_stock") or not as_of:
    sys.exit("Availability or timestamp unknown; history was not changed.")
stamp = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
if stamp.tzinfo is None:
    sys.exit("Timestamp needs a timezone; history was not changed.")
if history and history["request_id"] == answer["request_id"]:
    print(json.dumps(history["event"], indent=2))  # Replay the same delivery identity.
    sys.exit(0)
if history and stamp <= datetime.fromisoformat(history["as_of"].replace("Z", "+00:00")):
    sys.exit("Older snapshot; history was not changed.")
event = None
if history and history["availability"] != availability:
    event = {"event_id": answer["request_id"] + ":stock", "source": url,
             "from": history["availability"], "to": availability, "as_of": as_of}
# shortcut: one ordered writer; use an outbox/database for concurrent monitoring and delivery.
history_file.parent.mkdir(parents=True, exist_ok=True)
temporary = history_file.with_suffix(history_file.suffix + ".tmp")
temporary.write_text(json.dumps({"url": url, "availability": availability,
    "as_of": as_of, "request_id": answer["request_id"], "event": event}, indent=2))
temporary.replace(history_file)
print(json.dumps(event, indent=2))  # null on the baseline or an unchanged observation.
