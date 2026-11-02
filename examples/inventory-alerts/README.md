# Inventory alerts: known availability → local transition event

The stock comparison and local file update are directly in
[python.py](python.py) and [node.mjs](node.mjs).

```sh
python3 examples/inventory-alerts/python.py "$PRODUCT_URL" work/stock-python.json stock-check-001
# Alternative (use separate history if comparing implementations):
node examples/inventory-alerts/node.mjs "$PRODUCT_URL" work/stock-node.json stock-check-001
```

Arguments: **product URL, history file, stable request key**.
Supply `DATAWEBOT_API_KEY` via your environment. One new product observation
can cost up to 1 credit. Keep the same URL/key on an interrupted retry.

- The first known observation records a baseline and prints `null`.
- A change between `in_stock` and `out_of_stock` prints a JSON event.
- Missing/unknown availability or timestamps leave the prior state untouched.
- Retrying the latest observation prints the same event identity.
- A history file belongs to one exact URL. Run one writer at a time and process
  observations in order; older timestamps are refused.

Your code can read stdout and route an event to email, Slack or another channel.
Deduplicate delivery by `event_id`. These examples do not send notifications or
provide exactly-once delivery; add an outbox before using them as a production
alert service. A notification retry must not create a new extraction key.
