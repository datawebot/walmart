# Inventory transition alerts

Read a known product, keep the last **explicitly known** availability, and emit
a JSON event when it changes between `in_stock` and `out_of_stock`.
The first known observation establishes a baseline; it is not an alert.

## Run

Complete [setup](../../README.md#start-here) and choose one real Walmart product
URL. Run from the repository root:

```sh
node examples/inventory-alerts/node.mjs "$PRODUCT_URL" work/stock-node.json work/stock-node-001

# Python alternative; use its own history and choose one language per observation.
python3 examples/inventory-alerts/python.py "$PRODUCT_URL" work/stock-python.json work/stock-python-001
```

The third argument is the persistent history file; the fourth is this
observation's run directory. A new observation uses a new directory but **the
same history file and product URL**. Each new observation can cost up to 1 credit.

- Known availability transitions produce a JSON event with a stable identity.
- Null, unfamiliar stock wording and failed requests are unknown, not stock-outs.
  They must not replace the previous known state.
- Replaying the same observation does not create another transition or paid read.
- Histories are bound to a product; never share one between unrelated URLs.
- Use one writer per history/run directory and prevent overlapping scheduled runs.
- Process observation runs in order. Do not replay an older, not-yet-processed
  snapshot after a newer one has updated history; these examples are not an
  out-of-order event processor.

## Connect an alert channel

The script saves `alert-node.json` or `alert-python.json` in the run directory;
stdout reports completion, not the event payload. Node uses an `alert` property,
Python an `event` property; null means there is no transition to notify.
It deliberately does **not** require a Slack/Discord token or SMTP setup.
Your worker can read the saved event, route it to your notification service and
deduplicate delivery by `event_id`. Persist an
outbox before delivery if you need guaranteed retryable notifications.

Do not rerun extraction just because notification delivery failed. A crash
between local history update and stdout delivery is not a transactional outbox;
these samples do not guarantee exactly-once external delivery.

These are public-page availability signals, not warehouse counts, local-store
stock or a guarantee you can check out. See [safety](../../docs/safety.md).
