# Price-matching review proposal

Read one **known product URL** and compare its observed price with your own.
Both entry points perform a real, budget-limited REST request; neither changes
a store price. Run from the repository root.

## Run

Requires [setup](../../README.md#start-here), a product URL from a real search
result, your current price and its ISO currency code. Review the exact model,
variant and seller before supplying the URL.

```sh
# Set PRODUCT_URL to an observed Walmart product URL, not a search page.
node examples/price-matching/node.mjs "$PRODUCT_URL" 49.99 USD work/price-slot-001

# Python alternative; choose one language, not both for the same paid run.
python3 examples/price-matching/python.py "$PRODUCT_URL" 49.99 USD work/price-slot-001
```

Each new run can cost up to **1 credit**. The limit is sent to the service; a
higher current quote is refused. Scripts fetch exactly one selected product
without shopping fallback, preserve response pages, and save a JSON review
decision as `proposal-node.json` or `proposal-python.json` in the run directory.
Stdout reports completion, not the proposal payload. Missing/non-numeric prices or currency mismatch do not become zero or
an automatic price change. A changed URL cannot reuse an existing run checkpoint.

Use a new run directory for each intentional observation. Retry the same slot
with identical inputs and the same language/run directory to resume or replay;
do not delete its checkpoint after an uncertain network outcome.

## Schedule with cron (Linux example)

Provision `DATAWEBOT_API_KEY`, `PRODUCT_URL`, `OUR_PRICE` and `CURRENCY`
through your scheduler's environment. Do not put secrets in the crontab.
Create `work/` privately first. This example requires `flock` and absolute
Node/PATH configuration on your machine:

```cron
0 * * * * cd /absolute/path/to/walmart && flock -n work/price.lock /bin/sh -c 'node examples/price-matching/node.mjs "$PRODUCT_URL" "$OUR_PRICE" "$CURRENCY" "work/price-$(date -u +\%Y\%m\%dT\%H)"' >> work/price.log 2>> work/price-errors.log
```

The hour is the intentional observation slot; retries in that slot reuse the
same directory. Configure scheduler retries accordingly. For Python substitute
`python3 examples/price-matching/python.py`. Do not run Node and Python on the
same scheduled observation. Keep one writer and retain run artifacts.

## Before acting on a proposal

Verify model, variant, condition, seller, freshness, shipping and taxes in your
own workflow. The script checks URL identity and numeric/currency compatibility,
not commercial equivalence or a guaranteed-current checkout price. Review the
output before any downstream change. See [safety](../../docs/safety.md).
