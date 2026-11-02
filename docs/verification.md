# Verification and sample boundaries

These are standalone teaching scripts, not a shared SDK or production job
runner. Each includes its own visible payload, HTTP call, polling and use-case
logic. No script imports repository-local code or reads a hidden config file.

Run the offline checks (Python 3.11+ and Node 22.12+):

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

Tests replace the HTTP boundary with synthetic responses. They do not sign in,
spend credits, fetch source pages or send alerts.

## Limits kept visible in the code

- Caller supplies a stable request key. Retrying the same key/URL reuses the
  server request; these scripts do not provide a local checkpoint/recovery SDK.
- Do not reuse keys across recipes or for changed inputs. Use fresh keys only
  for intentional new observations.
- HTTP calls have 30-second timeouts and polling has a ten-minute budget.
  After an error respect the reported Retry-After before manually retrying;
  there is no automatic submission retry or server cancellation.
- Inventory history assumes one ordered writer. External delivery is not
  transactional; add an outbox for a production notification service.
- The MCP sample targets hosted stateless JSON HTTP, not SSE/session servers.
- The request router can hide upstream partial-result details. Rejecting exposed
  errors cannot guarantee completeness, current prices or source accuracy.

CLI documentation still targets the separately checked published
`@datawebot/cli@0.1.2`. Current server discovery wins over static examples.
No fresh live extraction or field-accuracy acceptance is claimed here.
