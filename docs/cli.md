# CLI setup and usage

Requires **Node.js 22.12+**. These commands target the published **0.1.2** release:

```sh
npm install --global @datawebot/cli@0.1.2
dtwb --version
dtwb --help
dtwb register you@example.com
```

Use your email, compare the approval code with the email, then explicitly approve
sign-in. The CLI waits and saves the key privately in `~/.datawebot/credentials`;
never paste a key in chat or commit it. `dtwb login` is also supported. Resume an
interrupted registration instead of starting repeated approval requests.

### Discover before spending

```sh
dtwb discover walmart.com
dtwb discover --blueprint-id walmart.search
dtwb discover --blueprint-id walmart.product_details
dtwb credits
```

Read allowed URL hosts, current versions, parameter/record schemas, page limits,
price and availability. Follow discovery's `next_cursor` if present, keeping the
same query. Stop on maintenance, access denial or insufficient credits.

### Run one search, then one selected product

**The next commands submit real work and may spend credits.** Replace the
example keyword with your intended query; keep the scope to one page initially.
Choose a unique idempotency key for each new logical run and save it before
submission. Reuse that key only with the identical inputs when recovering a lost
submission response.

```sh
umask 077
mkdir -p work

# Example key: change it for a genuinely new run, not for a retry.
dtwb extract walmart.search 'https://www.walmart.com/search?q=shoes' \
  --idempotency-key walmart-search-cli-example-001 > work/search.json
```

The CLI waits by default, then emits **one API result page** as JSON on stdout.
Progress, Job IDs and recovery keys go to stderr. Inspect the command's exit
code, status and errors before doing more work. Choose one relevant observed
product URL from the returned records; do not automatically enrich every result.

```sh
# Set PRODUCT_URL to one real product URL returned by your search.
dtwb extract walmart.product_details "$PRODUCT_URL" \
  --idempotency-key walmart-product-cli-example-001 > work/product.json
```

For asynchronous submission and recovery:

```sh
# This is an alternative to the earlier search, not an extra step to run.
dtwb extract --async walmart.search 'https://www.walmart.com/search?q=shoes' \
  --idempotency-key walmart-search-cli-example-001

# Replace JOB_UUID with the accepted Job ID from your own run.
dtwb status JOB_UUID
dtwb retrieve JOB_UUID --wait
dtwb retrieve JOB_UUID --page-size 100 --cursor 'NEXT_CURSOR_FROM_PREVIOUS_PAGE'
```

Keep page size fixed while following cursors. A timeout/Ctrl-C stops waiting,
not the server Job: resume with `retrieve --wait`, do not create another Job.
Exit 1 signals failure; exit 2 can mean invalid input or partial results. A
successful `status` command only means metadata was read; inspect the Job status.

**Release caveat:** the CLI repository's default-branch README may describe newer
commands. Published 0.1.2 was checked using `--help`; use `discover`, `extract`,
`status` and `retrieve` here, not unreleased `search`/`product` commands.
