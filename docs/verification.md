# Verification and limitations

Checked locally on **2026-11-02** with Node **22.22.3** and Python **3.14.6**.
Both brand configurations pass **30 Node tests and 24 Python tests** each.
All eight public entry points per repository also passed completed-cache smoke
checks from an unrelated working directory, without credentials or network.
CI runs the same suites on Node 22 / Python 3.11.

The examples are tested offline with synthetic API/MCP responses and temporary
files. No credentials, live sign-in, paid extraction, notification delivery or
commercial price changes are part of the test suite.

Run from the repository root:

```sh
npm test
python3 -m unittest discover -s tests -p 'test_*.py'
```

The suite covers request checkpointing and recovery, pending/paginated responses,
failure handling, URL validation and the use cases' data handling. MCP fixtures
reflect discovery/endpoints wrappers and completed answers without a REST-style
status. Check the actual test names/output for exact coverage.

Public API shapes were checked against the
[OpenAPI document](https://www.datawebot.com/openapi.json) and
[MCP guide](https://github.com/datawebot/datawebot-mcp). CLI setup targets
published `@datawebot/cli@0.1.2`, whose version/help were checked separately.
Live server discovery wins over static docs.

## Deliberate limits

- These are one-page examples, not a generic SDK, full scheduler or alert service.
- One writer per run directory/history file; lock scheduled jobs externally.
- Separate languages use separate checkpoints. Switching languages can spend again.
- Local history/stdout and external notification delivery are not one transaction.
  Add an outbox for production delivery guarantees.
- The API request router may present an upstream partial Job as done without
  exposing its errors. Scripts reject errors/partial markers they receive, but
  cannot detect hidden omissions. Field quality still requires review.
- Public snapshots do not guarantee current price, seller/variant equivalence,
  local inventory, complete coverage or checkout availability.
