# Price matching: one product → review proposal

Open [python.py](python.py) or [node.mjs](node.mjs). Each file contains the actual
HTTP request, polling loop and price comparison; there is no shared library.

```sh
# PRODUCT_URL must be a real Walmart product URL from a search result.
python3 examples/price-matching/python.py "$PRODUCT_URL" 49.99 USD price-check-001
# Alternative:
node examples/price-matching/node.mjs "$PRODUCT_URL" 49.99 USD price-check-001
```

Arguments: **product URL, your price, your currency, stable request key**.
Supply `DATAWEBOT_API_KEY` via your environment. Each new request costs at most
1 credit; the server refuses a higher quote.

The script prints JSON with `action: review_price_match` only for a lower,
known price in the same currency with a retrieval timestamp. Otherwise it prints
`no_proposal`. An empty/failed/mismatched listing stops without a proposal.
It never updates a store price. Review model, variant, seller, shipping and taxes
yourself before acting.

Retry an interrupted command with the **same URL and key**, not a new key.
For a new scheduled observation use a new time-slot key, and prevent overlaps.

Example cron entry (provision credentials and product variables outside cron):

```cron
0 * * * * cd /absolute/path/to/walmart && flock -n /tmp/walmart-price.lock /bin/sh -c 'python3 examples/price-matching/python.py "$PRODUCT_URL" "$OUR_PRICE" "$CURRENCY" "price-$(date -u +\%Y\%m\%dT\%H)"'
```
